# Примеры LogQL-запросов

Все записи Nextcloud:

```logql
{job="nextcloud_logs"}
```

Записи со словом `error` (поиск чувствителен к регистру):

```logql
{job="nextcloud_logs"} |= "error"
```

Поиск сообщений о входе в систему:

```logql
{job="nextcloud_logs"} |~ "(?i)login"
```

Число записей за пять минут по потокам:

```logql
count_over_time({job="nextcloud_logs"}[5m])
```

Суммарное число записей за пять минут:

```logql
sum(count_over_time({job="nextcloud_logs"}[5m]))
```

Временной диапазон Grafana должен охватывать моменты создания записей. Пустой результат фильтра не обязательно означает неисправность сборщика: совпадений может не быть.
