[CmdletBinding()]
param(
    [switch]$Execute,
    [string]$PostgresContainer = 'wifiguard-dev-postgres-1',
    [string]$TimescaleContainer = 'wifiguard-dev-timescaledb-1'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Psql {
    param(
        [Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][string]$Database,
        [Parameter(Mandatory)][string]$Sql
    )

    $output = & docker exec $Container psql -U wifiguard -d $Database -v ON_ERROR_STOP=1 -At -c $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "psql failed in $Container with exit code $LASTEXITCODE"
    }
    return @($output)
}

$deviceSql = @"
SELECT d.id::text
FROM devices d
JOIN users u ON u.id = d.owner_user_id
WHERE u.email LIKE 'software-e2e-%@example.invalid'
   OR u.email LIKE 'hardware-e2e-%@example.invalid'
   OR d.name IN ('ACFD software E2E', 'Segmentation software E2E', 'Physical model E2E')
ORDER BY d.id;
"@

$deviceIds = @(Invoke-Psql -Container $PostgresContainer -Database 'wifiguard' -Sql $deviceSql) |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

$countSql = @"
SELECT 'users=' || count(*) FROM users
WHERE email LIKE 'software-e2e-%@example.invalid'
   OR email LIKE 'hardware-e2e-%@example.invalid';
SELECT 'devices=' || count(*) FROM devices
WHERE name IN ('ACFD software E2E', 'Segmentation software E2E', 'Physical model E2E');
SELECT 'falls=' || count(*) FROM fall_events
WHERE model_version LIKE 'software-e2e-%'
   OR model_version LIKE 'synthetic-%';
SELECT 'temporary_recipients=' || count(*) FROM recipients
WHERE name = 'Temporary external email E2E';
"@

Write-Host '[local E2E residue]'
Invoke-Psql -Container $PostgresContainer -Database 'wifiguard' -Sql $countSql | ForEach-Object {
    Write-Host $_
}
Write-Host "telemetry_device_ids=$($deviceIds.Count)"

if (-not $Execute) {
    Write-Host 'DRY_RUN_ONLY: pass -Execute to remove only the rows listed above.'
    exit 0
}

foreach ($deviceId in $deviceIds) {
    if ($deviceId -notmatch '^[0-9a-fA-F-]{36}$') {
        throw "Unexpected device UUID returned by PostgreSQL: $deviceId"
    }
    Invoke-Psql -Container $TimescaleContainer -Database 'wifiguard_ts' -Sql (
        "DELETE FROM presence_samples WHERE device_id = '$deviceId';"
    ) | Out-Null
}

$cleanupSql = @"
BEGIN;
DELETE FROM fall_events
WHERE model_version LIKE 'software-e2e-%'
   OR model_version LIKE 'synthetic-%';
DELETE FROM recipients
WHERE name = 'Temporary external email E2E';
DELETE FROM users
WHERE email LIKE 'software-e2e-%@example.invalid'
   OR email LIKE 'hardware-e2e-%@example.invalid';
DELETE FROM devices
WHERE name IN ('ACFD software E2E', 'Segmentation software E2E', 'Physical model E2E');
COMMIT;
"@

Invoke-Psql -Container $PostgresContainer -Database 'wifiguard' -Sql $cleanupSql | Out-Null

Write-Host '[after cleanup]'
Invoke-Psql -Container $PostgresContainer -Database 'wifiguard' -Sql $countSql | ForEach-Object {
    Write-Host $_
}
Write-Host 'LOCAL_E2E_TEST_DATA_CLEANED'
