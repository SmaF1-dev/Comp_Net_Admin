$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose configuration is invalid.' }

    docker compose ps -a
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Docker Compose containers.' }

    $expected = @('nextcloud', 'loki', 'promtail', 'postgres-zabbix', 'zabbix-server', 'zabbix-web', 'grafana')
    $running = @(docker compose ps --services --status running)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect running services.' }

    $missing = @($expected | Where-Object { $_ -notin $running })
    if ($missing.Count -gt 0) { throw ('Not running: ' + ($missing -join ', ')) }

    $dbHealth = docker inspect --format '{{.State.Health.Status}}' lab1-postgres-zabbix
    if ($LASTEXITCODE -ne 0 -or $dbHealth -ne 'healthy') {
        throw "PostgreSQL healthcheck is not healthy: $dbHealth"
    }

    $lokiReady = $false
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        $response = (curl.exe --silent --fail --max-time 5 http://localhost:3100/ready)
        if ($LASTEXITCODE -eq 0 -and ($response -join '') -eq 'ready') {
            $lokiReady = $true
            break
        }
        if ($attempt -lt 20) { Start-Sleep -Seconds 2 }
    }
    if (-not $lokiReady) {
        throw 'Loki readiness endpoint did not return ready. Check: docker compose logs --tail=100 loki'
    }

    Write-Host 'PASS: 7 services running, PostgreSQL healthy, Loki ready.' -ForegroundColor Green
}
finally {
    Pop-Location
}
