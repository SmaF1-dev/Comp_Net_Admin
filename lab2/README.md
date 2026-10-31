# Лабораторная работа №2 — Loki + Zabbix + Grafana

**Задача:** организовать мониторинг Nextcloud с помощью Zabbix, сбор журналов событий с помощью Promtail и Loki и отображение полученных данных в Grafana.

В репозитории находятся проверенные конфигурационные файлы, две информационные панели Grafana с автоматической загрузкой из файлов, сценарии проверки для PowerShell и [полный отчёт со снимками экрана](REPORT.md). Все снимки экрана получены в ходе выполнения лабораторной работы.

## Архитектура

**Сбор журналов:** Nextcloud записывает события в `nextcloud.log`. Promtail читает этот файл из общего тома Docker и передаёт записи в Loki. Grafana выполняет запросы на языке LogQL к Loki и отображает полученные записи в таблице.

**Мониторинг:** сервер Zabbix периодически обращается к Nextcloud по адресу `/status.php`, сохраняет результаты в PostgreSQL и предоставляет их через веб-интерфейс Zabbix. Исходный текстовый показатель `nextcloud.ping` принимает значения `healthy` и `unhealthy` и используется в условии обнаружения проблемы. Зависимый числовой показатель `nextcloud.healthy` преобразует их в `1` и `0`; Grafana получает это значение через программный интерфейс Zabbix и отображает цветную панель состояния.

Все семь сервисов работают в одной сети Docker Compose. Внешние порты доступны только по адресу `127.0.0.1`, поскольку стенд предназначен для локального учебного использования.

| Сервис | Адрес на компьютере | Функция |
| --- | --- | --- |
| Nextcloud | `http://localhost:8080` | Тестовое приложение |
| Loki | `http://localhost:3100` | Поиск и хранение записей журнала |
| Promtail | — | Чтение файла журнала событий |
| PostgreSQL | — | База Zabbix |
| Zabbix Server | `localhost:10051` | Проверки по HTTP и обнаружение проблем |
| Zabbix Web | `http://localhost:8082` | Управление мониторингом |
| Grafana | `http://localhost:3000` | Информационные панели |

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

Для запуска необходимы Docker Desktop в режиме контейнеров Linux и Docker Compose.

```powershell
Copy-Item .env.example .env
docker compose config --quiet
docker compose up -d
docker compose ps -a
powershell -ExecutionPolicy Bypass -File .\scripts\check-stage1.ps1
```

Если порт `8080` уже занят, установите `NEXTCLOUD_PORT=8083` в `.env` перед запуском. На первом старте Loki может временно отвечать `503` на `/ready`: сценарий выполняет повторные проверки. В конфигурации используются Grafana `11.2.0` и совместимый плагин Zabbix `4.5.6`.

В конфигурации сохранены технические имена контейнеров `lab1-*`, заданные при первоначальном запуске стенда. **Сдаваемая лабораторная работа имеет номер 2.** Имена контейнеров оставлены без изменений, поскольку они используются в проверенных настройках и на снимках экрана.

## Инициализация Nextcloud

Откройте `http://localhost:8080` (или свой порт) и создайте локального администратора. Для тестовой установки можно использовать SQLite; PostgreSQL из Compose предназначен для Zabbix. Проверьте журнал:

```powershell
docker compose exec -T -u www-data nextcloud php occ config:system:get datadirectory
docker compose exec -T nextcloud sh -c 'ls -lh /var/www/html/data/nextcloud.log && tail -n 5 /var/www/html/data/nextcloud.log'
```

## Передача журналов событий из Promtail в Loki

```powershell
docker compose exec -T promtail sh -c 'ls -lh /opt/nc_data/nextcloud.log'
docker compose logs promtail | Select-String 'nextcloud.log'
powershell -ExecutionPolicy Bypass -File .\scripts\check-logs.ps1
```

Метка записей — `job="nextcloud_logs"`, источник файлов — `/opt/nc_data/*.log`. В разделе запросов Grafana проверены выбор всех записей и фильтрация диагностических событий запросом `{job="nextcloud_logs"} |= "LAB2_PIPELINE_"` (скриншот `screenshots/15-logql-filter.png`). Запросы и дополнительные примеры приведены в [QUERIES.md](QUERIES.md).

Если нужно доказать поступление **новой** записи, используйте:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-loki-live.ps1
```

Сценарий добавляет в `nextcloud.log` одну явно помеченную диагностическую запись и ищет её через программный интерфейс Loki. После успешного выполнения проверки выберите в Grafana временной диапазон за последний час и обновите панель журнала событий.

## Настройка Zabbix

1. Откройте `http://localhost:8082`, войдите с начальными учётными данными Zabbix `Admin` / `zabbix`.
2. В разделе сбора данных откройте шаблоны и выберите импорт (`Data collection → Templates → Import`). Импортируйте файл `template.yml`: он содержит текстовый показатель `nextcloud.ping`, условие обнаружения проблемы и зависимый числовой показатель `nextcloud.healthy`.
3. Добавьте внутреннее имя сервиса Nextcloud в список доверенных доменов:

   ```powershell
   docker compose exec -T -u www-data nextcloud php occ config:system:set trusted_domains 1 --value=nextcloud
   ```

4. В разделе сбора данных откройте список узлов и создайте новый (`Data collection → Hosts → Create host`). Укажите техническое имя `nextcloud`, отображаемое имя `Nextcloud Server`, группу `Applications` и шаблон `Test ping template`. Для проверки по HTTP отдельный агент не требуется.
5. В разделе мониторинга, на странице последних данных (`Monitoring → Latest data`), проверьте значения `nextcloud.ping = healthy` и `nextcloud.healthy = 1`.

Если шаблон уже был импортирован ранее, повторный импорт должен разрешать обновление существующего шаблона и создание нового зависимого элемента.

## Проверка обнаружения аварийного состояния

```powershell
docker compose exec -T -u www-data nextcloud php occ maintenance:mode --on
```

В разделе проблем мониторинга (`Monitoring → Problems`) должно появиться событие `Nextcloud is in maintenance mode`. При этом показатели принимают значения `nextcloud.ping = unhealthy` и `nextcloud.healthy = 0`. **Обязательно восстановите сервис:**

```powershell
docker compose exec -T -u www-data nextcloud php occ maintenance:mode --off
```

После очередного опроса значения возвращаются к `healthy` / `1`, а событие закрывается.

## Grafana

По адресу `http://localhost:3000` проверьте, что дополнение **Zabbix** версии `4.5.6` активно, а источники данных **Loki** (`http://loki:3100`) и **Zabbix** (`http://zabbix-web:8080/api_jsonrpc.php`) созданы автоматически. В разделе запросов Grafana выражение `{job="nextcloud_logs"}` должно возвращать записи при наличии событий в выбранном временном диапазоне.

В разделе информационных панелей Grafana, в папке `Laboratory 2`, находятся две панели, автоматически загружаемые из файлов:

- **Nextcloud — состояние (Zabbix):** цветной индикатор, использующий показатель `nextcloud.healthy`; значению `1` соответствует зелёный фон, значению `0` — красный.
- **Nextcloud — логи (Loki):** таблица записей журнала по запросу `{job="nextcloud_logs"}`.

Файлам панелей назначены отдельные уникальные идентификаторы `lab2-nextcloud-status` и `lab2-nextcloud-logs`. **Сохраняйте текущие имена файлов** `lab2-nextcloud-*.json`: это необходимо для повторной загрузки проверенной конфигурации. При развёртывании нового стенда панели автоматически создаются из файлов. В ранее использованной Grafana могут остаться старые панели в папке `Laboratory 1`; к сдаваемой работе №2 они не относятся.

## Отчёт и проверка

- [REPORT.md](REPORT.md) — ход выполнения, результаты и ответы на три контрольных вопроса.
- [screenshots/README.md](screenshots/README.md) — перечень доказательных скриншотов.
- [QUERIES.md](QUERIES.md) — примеры запросов Loki.

**Ограничения:** стенд рассчитан на локальное использование; учебные пароли нельзя применять на сервере с доступом из Интернета. В конфигурации Docker Compose для Loki нет отдельного постоянного тома, поэтому после пересоздания контейнера ранее полученные записи могут исчезнуть. Журнал Nextcloud хранится в томе `nextcloud-data`, а поступление новых записей проверяется сценарием `check-loki-live.ps1`.

Для остановки стенда с сохранением данных:

```powershell
docker compose down
```

Команда `docker compose down -v` удаляет тома и находящиеся в них данные; при обычной остановке **не используйте** параметр `-v`.
