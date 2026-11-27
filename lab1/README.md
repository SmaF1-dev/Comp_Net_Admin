# ЛР1. Отказоустойчивый кластер PostgreSQL

Стенд включает два узла PostgreSQL 18.7 под управлением Patroni 4.1.5, три узла etcd 3.7.4 и прокси Envoy. Patroni определяет роли `pg1` и `pg2`. Envoy направляет новые подключения с `127.0.0.1:15432` на текущего лидера.

## Файлы

| Файл | Назначение |
|---|---|
| [`compose.yaml`](compose.yaml) | Сервисы, адреса узлов, проверки готовности и отдельные тома данных. |
| [`Dockerfile`](Dockerfile) | Построение общего образа PostgreSQL и Patroni. Контейнер запускает Patroni, который сам управляет процессом PostgreSQL. |
| [`patroni.yml`](patroni.yml) | Общая конфигурация Patroni: адреса etcd, правила подключения к PostgreSQL и параметры начальной инициализации. Имена и адреса конкретных узлов передаются из Compose. |
| [`envoy.yaml`](envoy.yaml) | TCP-прокси PostgreSQL. Envoy определяет лидера по ответу Patroni на `GET /primary` на порту `8008`. |
| [`.dockerignore`](.dockerignore) | В контекст сборки попадают только Dockerfile и конфигурация Patroni. |

## Запуск и проверка стенда

Нужен Docker с Linux-контейнерами и Docker Compose v2. Из корня репозитория:

```sh
cd lab1
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
```

Проверка доступности трёх узлов etcd и поиск его лидера:

```sh
docker compose exec etcd1 etcdctl --endpoints=http://etcd1:2379 endpoint health --cluster
docker compose exec etcd1 etcdctl --endpoints=http://etcd1:2379 endpoint status --cluster --write-out=table
```

Проверка ролей PostgreSQL и готовности синхронной реплики:

```sh
docker compose exec pg1 patronictl -c /etc/patroni.yml list lab1
```

Ожидаемый результат: все три узла etcd здоровы, один отмечен как лидер etcd. В списке Patroni один PostgreSQL должен иметь роль `Leader` и состояние `running`, второй — роль `Sync Standby` и состояние `streaming`. Назначение синхронной реплики может занять несколько циклов Patroni после запуска. Если роли ещё меняются, повторите `patronictl list`. Для отчёта сохраните вывод этих команд после стабилизации кластера.

Если запуск прервался, проверьте состояние и логи:

```sh
docker compose ps -a
docker compose logs --tail=100 etcd1 etcd2 etcd3 pg1 pg2 envoy
```

`docker compose down` останавливает стек и сохраняет тома. Повторный запуск поднимет кластер с теми же данными. Параметры из `bootstrap.dcs` применяются при первичной инициализации. Для уже созданного кластера динамическую конфигурацию меняют через `patronictl edit-config`.

Пароли по умолчанию предназначены для локального учебного стенда. Перед первым запуском их можно задать через `LAB1_POSTGRES_PASSWORD` и `LAB1_REPLICATION_PASSWORD` в локальном `.env`.

## Ручная проверка репликации через SQL

Выполните команды из корня репозитория в PowerShell. Сначала определите текущие роли.

```powershell
docker compose -f lab1/compose.yaml exec -T pg1 patronictl -c /etc/patroni.yml list lab1
```

В примере `pg1` — лидер, `pg2` — синхронная реплика. Если роли поменялись, подставьте имена узлов по выводу Patroni. Подключения выполняются через локальный сокет внутри контейнеров.

1. Подключитесь к лидеру.

   ```powershell
   docker compose -f lab1/compose.yaml exec --user postgres pg1 psql -X -U postgres -d postgres
   ```

   Выполните команды в `psql`. Первый запрос должен вернуть `f`. Скопируйте UUID, который вернёт `INSERT`, и выйдите командой `\q`.

   ```sql
   SELECT pg_is_in_recovery();

   CREATE SCHEMA IF NOT EXISTS lab_replication;
   CREATE TABLE IF NOT EXISTS lab_replication.demo (
       id uuid PRIMARY KEY,
       message text NOT NULL
   );
   INSERT INTO lab_replication.demo (id, message)
   VALUES (gen_random_uuid(), 'replication-check')
   RETURNING id, message;
   ```

2. Подключитесь к реплике.

   ```powershell
   docker compose -f lab1/compose.yaml exec --user postgres pg2 psql -X -U postgres -d postgres
   ```

   Первый запрос должен вернуть `t`. Подставьте сохранённый UUID вместо `<UUID>`. Второй запрос должен вернуть ту же строку, которую вернул `INSERT` на лидере. Если строка ещё не появилась, повторите `SELECT` после того, как реплика применит WAL.

   ```sql
   SELECT pg_is_in_recovery();
   SELECT id, message FROM lab_replication.demo WHERE id = '<UUID>';
   ```

3. В том же сеансе `psql` проверьте, что реплика отклоняет запись. Транзакция откатывается, даже если узел успел стать лидером и `INSERT` выполнился.

   ```sql
   BEGIN;
   INSERT INTO lab_replication.demo (id, message)
   VALUES (gen_random_uuid(), 'standby-write');
   \echo :SQLSTATE
   ROLLBACK;
   ```

   Ожидаемый код — `25006` (`read_only_sql_transaction`). Для отчёта сохраните результаты `INSERT` на лидере, `SELECT` на реплике и код отказа записи.

## Подключение через Envoy

С хоста, где установлен `psql`, проверьте подключение к опубликованному порту.

```powershell
psql -h 127.0.0.1 -p 15432 -U postgres -d postgres -W `
    -c "SELECT pg_is_in_recovery() AS in_recovery, inet_server_addr() AS server_addr;"
```

Введите действующий пароль роли `postgres`. Для нового кластера со стандартной конфигурацией это `lab1-postgres`. В существующем томе пароль роли может отличаться от текущего значения `LAB1_POSTGRES_PASSWORD`. Результат `in_recovery = f` подтверждает подключение к основному узлу.
