# Лабораторная работа №2 — Loki + Zabbix + Grafana

**Задача:** подключить к Nextcloud мониторинг (Zabbix), сбор логов (Promtail и Loki) и визуализацию в Grafana.

В репозитории находятся рабочие конфигурации, два provisioned JSON-дашборда, диагностические PowerShell-скрипты и [полный отчёт со скриншотами](REPORT.md). Все скриншоты сделаны при реальном выполнении лабораторной работы.

## Архитектура

**Логирование:** Nextcloud записывает события в `nextcloud.log`. Promtail читает этот файл из общего Docker volume и пересылает записи в Loki. Grafana обращается к Loki по LogQL-запросу и показывает таблицу логов.

**Мониторинг:** Zabbix Server периодически опрашивает Nextcloud по адресу `/status.php`, сохраняет результаты в PostgreSQL и отображает их через Zabbix Web. Исходный текстовый элемент `nextcloud.ping` показывает `healthy` / `unhealthy` и управляет триггером. Зависимый числовой элемент `nextcloud.healthy` преобразует состояния в `1` / `0`; Grafana получает его через API Zabbix и отображает зелёную/красную Stat-панель.

Все семь сервисов находятся в одной Docker Compose-сети. Порты проброшены только на `127.0.0.1` для локального учебного использования.

| Сервис | Адрес на компьютере | Функция |
| --- | --- | --- |
| Nextcloud | `http://localhost:8080` | Тестовое приложение |
| Loki | `http://localhost:3100` | Поиск и хранение логов |
| Promtail | — | Считывание лог-файла |
| PostgreSQL | — | База Zabbix |
| Zabbix Server | `localhost:10051` | HTTP-проверка и триггеры |
| Zabbix Web | `http://localhost:8082` | Управление мониторингом |
| Grafana | `http://localhost:3000` | Дашборды |

Внешний порт Nextcloud задаётся через `NEXTCLOUD_PORT` в `.env`.

## Структура проекта

```text
.
├── .env.example
├── .gitignore
├── docker-compose.yml
├── promtail_config.yml
├── template.yml
├── grafana/
│   ├── dashboards/
│   │   ├── lab2-nextcloud-logs.json
│   │   └── lab2-nextcloud-status.json
│   └── provisioning/
│       ├── dashboards/dashboards.yml
│       └── datasources/datasources.yml
├── scripts/
│   ├── check-stage1.ps1
│   ├── check-logs.ps1
│   └── check-loki-live.ps1
├── screenshots/
│   └── *.png
├── QUERIES.md
├── REPORT.md
└── README.md
```

## Запуск (Windows 11 / PowerShell)

Требуются Docker Desktop с Linux Containers и Docker Compose.

```powershell
Copy-Item .env.example .env
docker compose config --quiet
docker compose up -d
docker compose ps -a
powershell -ExecutionPolicy Bypass -File .\scripts\check-stage1.ps1
```

Если порт `8080` уже занят, установите `NEXTCLOUD_PORT=8083` в `.env` перед запуском. На первом старте Loki может временно отвечать `503` на `/ready`: скрипт делает повторные проверки. В конфигурации используются Grafana `11.2.0` и совместимый плагин Zabbix `4.5.6`.

В конфигурации контейнерам присвоены имена `lab1-*`, оставшиеся от первоначального локального стенда. **Номер сдаваемой работы — №2**; эти технические имена сохранены, чтобы соответствовать фактически протестированной конфигурации и приведённым скриншотам.

## Инициализация Nextcloud

Откройте `http://localhost:8080` (или свой порт) и создайте локального администратора. Для тестовой установки можно использовать SQLite; PostgreSQL из Compose предназначен для Zabbix. Проверьте журнал:

```powershell
docker compose exec -T -u www-data nextcloud php occ config:system:get datadirectory
docker compose exec -T nextcloud sh -c 'ls -lh /var/www/html/data/nextcloud.log && tail -n 5 /var/www/html/data/nextcloud.log'
```

## Сбор логов Promtail → Loki

```powershell
docker compose exec -T promtail sh -c 'ls -lh /opt/nc_data/nextcloud.log'
docker compose logs promtail | Select-String 'nextcloud.log'
powershell -ExecutionPolicy Bypass -File .\scripts\check-logs.ps1
```

Метка логов — `job="nextcloud_logs"`, источник файлов — `/opt/nc_data/*.log`. Примеры LogQL доступны в [QUERIES.md](QUERIES.md).

Если нужно доказать поступление **новой** записи, используйте:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-loki-live.ps1
```

Скрипт добавляет в `nextcloud.log` одну явно помеченную диагностическую запись и ищет её через API Loki. После `PASS` в Grafana выберите диапазон `Last 1 hour` и обновите дашборд логов.

## Настройка Zabbix

1. Откройте `http://localhost:8082`, войдите с начальными учётными данными Zabbix `Admin` / `zabbix`.
2. **Data collection → Templates → Import**: импортируйте `template.yml` (новые шаблон и элементы). Файл включает текстовый HTTP-элемент `nextcloud.ping`, триггер и зависимый числовой `nextcloud.healthy`.
3. Добавьте внутреннее имя сервиса Nextcloud в trusted domains:

   ```powershell
   docker compose exec -T -u www-data nextcloud php occ config:system:set trusted_domains 1 --value=nextcloud
   ```

4. **Data collection → Hosts → Create host**: `Host name = nextcloud`, `Visible name = Nextcloud Server`, группа `Applications`, шаблон `Test ping template`. Интерфейс агента для HTTP Agent не нужен.
5. В **Monitoring → Latest data** проверьте `Nextcloud: ping service = healthy` и `Nextcloud: healthy (numeric) = 1`.

Если шаблон уже был импортирован ранее, повторный импорт должен разрешать обновление существующего шаблона и создание нового зависимого элемента.

## Тест срабатывания триггера

```powershell
docker compose exec -T -u www-data nextcloud php occ maintenance:mode --on
```

В `Monitoring → Problems` должно появиться событие `Nextcloud is in maintenance mode`, при этом `nextcloud.ping = unhealthy`, `nextcloud.healthy = 0`. **Обязательно восстановите сервис:**

```powershell
docker compose exec -T -u www-data nextcloud php occ maintenance:mode --off
```

После очередного опроса значения возвращаются к `healthy` / `1`, а событие закрывается.

## Grafana

В `http://localhost:3000` проверьте установку/активацию плагина **Zabbix** (версия `4.5.6`) и автоматически созданные источники **Loki** (`http://loki:3100`) и **Zabbix** (`http://zabbix-web:8080/api_jsonrpc.php`). В Explore запрос `{job="nextcloud_logs"}` должен возвращать строки при наличии событий в указанном диапазоне.

В **Dashboards → Laboratory 2** находятся два provisioned дашборда:

- **Nextcloud — состояние (Zabbix):** Stat-панель, числовой `nextcloud.healthy`, `1 = HEALTHY` (зелёный), `0 = UNHEALTHY` (красный).
- **Nextcloud — логи (Loki):** Table-панель, запрос `{job="nextcloud_logs"}`.

JSON-файлы используют отдельные UID `lab2-nextcloud-status` и `lab2-nextcloud-logs`. Важно сохранить **именно текущие имена файлов** `lab2-nextcloud-*.json`. Для повторного развёртывания из чистого стенда достаточно файлового provisioning; при обновлении существующего стенда могут остаться ранее созданные дашборды в `Laboratory 1` — к сдаваемой ЛР №2 они не относятся.

## Отчёт и проверка

- [REPORT.md](REPORT.md) — ход выполнения, результаты и ответы на три контрольных вопроса.
- [screenshots/README.md](screenshots/README.md) — перечень доказательных скриншотов.
- [QUERIES.md](QUERIES.md) — примеры запросов Loki.

**Ограничения:** стенд локальный, учебные пароли нельзя использовать на открытом сервере. В исходном Compose для Loki нет отдельного persistent volume: после пересоздания контейнера история может исчезнуть. Файловый журнал Nextcloud сохраняется в `nextcloud-data`, а поступление новой записи можно подтвердить `check-loki-live.ps1`.

Для остановки стенда с сохранением данных:

```powershell
docker compose down
```

`docker compose down -v` удаляет volumes и данные; для обычной остановки **не используйте** `-v`.
