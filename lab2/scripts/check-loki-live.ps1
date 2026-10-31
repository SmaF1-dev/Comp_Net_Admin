$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    $logPath = '/var/www/html/data/nextcloud.log'
    docker compose exec -T nextcloud sh -c "test -f $logPath"
    if ($LASTEXITCODE -ne 0) { throw 'Файл журнала Nextcloud не найден.' }

    $marker = 'LAB2_PIPELINE_' + [guid]::NewGuid().ToString('N').Substring(0, 12)
    $entry = [ordered]@{
        reqId = $marker
        level = 2
        time = [DateTimeOffset]::UtcNow.ToString('o')
        app = 'lab2-diagnostic'
        message = $marker
    } | ConvertTo-Json -Compress

    Write-Host 'Добавляем в журнал Nextcloud одну диагностическую запись в формате JSON...'
    $entry | docker compose exec -T -u www-data nextcloud sh -c "cat >> $logPath"
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось добавить диагностическую запись в журнал Nextcloud.' }

    $selector = '{job="nextcloud_logs"} |= "' + $marker + '"'
    $encoded = [uri]::EscapeDataString($selector)
    $url = "http://localhost:3100/loki/api/v1/query_range?query=$encoded&since=1h&limit=20"

    $found = $false
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        Start-Sleep -Seconds 2
        try {
            $response = Invoke-RestMethod -Method Get -Uri $url -TimeoutSec 8
        }
        catch {
            Write-Host "Попытка обращения к Loki №${attempt}: $($_.Exception.Message)"
            continue
        }

        foreach ($stream in @($response.data.result)) {
            foreach ($record in @($stream.values)) {
                if ($record[1] -like "*$marker*") {
                    $found = $true
                    break
                }
            }
            if ($found) { break }
        }
        if ($found) { break }
    }

    if (-not $found) {
        Write-Host 'Диагностическая запись не обнаружена в Loki. Последние сообщения Promtail:' -ForegroundColor Yellow
        docker compose logs --tail=25 promtail
        Write-Host 'Последние сообщения Loki:' -ForegroundColor Yellow
        docker compose logs --tail=25 loki
        throw "ОШИБКА: Loki не вернул диагностическую запись $marker."
    }

    Write-Host "УСПЕШНО: запись из файла Nextcloud передана через Promtail в Loki. Идентификатор: $marker" -ForegroundColor Green
    Write-Host 'В Grafana откройте папку Laboratory 2, панель журналов Nextcloud; выберите период за последний час и обновите данные.'
}
finally {
    Pop-Location
}
