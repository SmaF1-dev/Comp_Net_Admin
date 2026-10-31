$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    $logPath = '/var/www/html/data/nextcloud.log'
    docker compose exec -T nextcloud sh -c "test -f $logPath"
    if ($LASTEXITCODE -ne 0) { throw 'Nextcloud log file does not exist.' }

    $marker = 'LAB2_PIPELINE_' + [guid]::NewGuid().ToString('N').Substring(0, 12)
    $entry = [ordered]@{
        reqId = $marker
        level = 2
        time = [DateTimeOffset]::UtcNow.ToString('o')
        app = 'lab2-diagnostic'
        message = $marker
    } | ConvertTo-Json -Compress

    Write-Host 'Appending one clearly labeled diagnostic JSON entry to Nextcloud log...'
    $entry | docker compose exec -T -u www-data nextcloud sh -c "cat >> $logPath"
    if ($LASTEXITCODE -ne 0) { throw 'Could not append diagnostic entry to Nextcloud log.' }

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
            Write-Host "Loki query attempt ${attempt}: $($_.Exception.Message)"
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
        Write-Host 'No diagnostic entry appeared in Loki. Recent Promtail logs:' -ForegroundColor Yellow
        docker compose logs --tail=25 promtail
        Write-Host 'Recent Loki logs:' -ForegroundColor Yellow
        docker compose logs --tail=25 loki
        throw "FAIL: Loki did not return diagnostic entry $marker."
    }

    Write-Host "PASS: Nextcloud file -> Promtail -> Loki. Diagnostic entry: $marker" -ForegroundColor Green
    Write-Host 'Grafana: Dashboard > Laboratory 2 > Nextcloud logs; set Last 1 hour and refresh.'
}
finally {
    Pop-Location
}
