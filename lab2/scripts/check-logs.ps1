$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    docker compose exec -T nextcloud sh -c 'test -s /var/www/html/data/nextcloud.log'
    if ($LASTEXITCODE -ne 0) { throw 'Файл журнала Nextcloud отсутствует или пуст.' }

    $query = [uri]::EscapeDataString('{job="nextcloud_logs"}')
    $url = "http://localhost:3100/loki/api/v1/query_range?query=$query&limit=20"
    $result = Invoke-RestMethod -Uri $url -Method Get

    if ($result.status -ne 'success' -or $result.data.result.Count -eq 0) {
        throw 'В Loki не найдены записи Nextcloud. Создайте новое событие и повторите проверку.'
    }

    Write-Host 'УСПЕШНО: файл журнала Nextcloud существует, Loki возвращает записи.' -ForegroundColor Green
}
finally {
    Pop-Location
}
