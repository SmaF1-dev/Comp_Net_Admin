$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Push-Location $repo
try {
    docker compose exec -T nextcloud sh -c 'test -s /var/www/html/data/nextcloud.log'
    if ($LASTEXITCODE -ne 0) { throw 'Nextcloud log is absent or empty.' }

    $query = [uri]::EscapeDataString('{job="nextcloud_logs"}')
    $url = "http://localhost:3100/loki/api/v1/query_range?query=$query&limit=20"
    $result = Invoke-RestMethod -Uri $url -Method Get

    if ($result.status -ne 'success' -or $result.data.result.Count -eq 0) {
        throw 'No Nextcloud log streams found in Loki. Generate fresh logs and retry.'
    }

    Write-Host 'PASS: Nextcloud log exists and Loki returned log streams.' -ForegroundColor Green
}
finally {
    Pop-Location
}
