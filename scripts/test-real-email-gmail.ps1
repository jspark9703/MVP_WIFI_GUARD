[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')]
    [string]$RecipientEmail,

    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9._%+-]+@gmail\.com$')]
    [string]$GmailAddress,

    [Parameter(Mandatory)]
    [Guid]$OwnerUserId,

    [Parameter(Mandatory)]
    [Guid]$DeviceId
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot 'WIFIGUARD-BACKEND'
$composeDir = Join-Path $backendRoot 'compose'
$composeFile = Join-Path $composeDir 'docker-compose.dev.yml'
$envFile = Join-Path $composeDir '.env'
$apiContainer = 'wifiguard-dev-api-1'
$postgresContainer = 'wifiguard-dev-postgres-1'
$recipientId = [Guid]::NewGuid()
$tenantId = "home-$ownerUserId"

function Invoke-Checked {
    param(
        [Parameter(Mandatory)]
        [scriptblock]$Command,
        [Parameter(Mandatory)]
        [string]$Description
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Wait-Api {
    param([int]$TimeoutSeconds = 90)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 3
            if ($null -ne $health) {
                return $health
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    throw 'API did not become ready before the timeout.'
}

function Wait-KafkaIngestGroup {
    param([int]$TimeoutSeconds = 90)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $stateOutput = & docker exec wifiguard-dev-kafka-1 `
            /opt/kafka/bin/kafka-consumer-groups.sh `
            --bootstrap-server localhost:9092 `
            --describe `
            --group wifiguard-ingest `
            --state 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0 -and $stateOutput -match '(?m)^wifiguard-ingest\s+.*\sStable\s+1\s*$') {
            Write-Host 'wifiguard-ingest Kafka group STABLE'
            return
        }
        Start-Sleep -Seconds 2
    }
    throw 'Kafka ingest consumer group did not become stable before the timeout.'
}

function Set-ProcessEnvironment {
    param([hashtable]$Values)

    foreach ($item in $Values.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($item.Key, [string]$item.Value, 'Process')
    }
}

function Restore-ProcessEnvironment {
    param([hashtable]$Values)

    foreach ($item in $Values.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($item.Key, $item.Value, 'Process')
    }
}

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing local Compose environment file: $envFile"
}

$smtpKeys = @(
    'SMTP_HOST',
    'SMTP_PORT',
    'SMTP_USERNAME',
    'SMTP_PASSWORD',
    'SMTP_FROM',
    'SMTP_STARTTLS'
)
$previousEnvironment = @{}
foreach ($key in $smtpKeys) {
    $previousEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}

Write-Host '[preflight]'
Write-Host 'Google 2-Step Verification must be enabled and a 16-digit app password must exist.'
Write-Host 'Use the Google app password, not the normal account password.'
$tcp = Test-NetConnection smtp.gmail.com -Port 587 -WarningAction SilentlyContinue
if (-not $tcp.TcpTestSucceeded) {
    throw 'Cannot connect to smtp.gmail.com:587 from this PC.'
}
Write-Host 'smtp.gmail.com:587 TCP PASS'

$securePassword = Read-Host 'Enter the Google 16-digit app password (input is hidden)' -AsSecureString
$passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
$plainPassword = $null
$syntheticModelVersion = "synthetic-external-email-e2e:$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"

try {
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr)
    # Google displays app passwords in four groups separated by spaces. SMTP
    # authentication expects the same 16 characters without display spacing.
    $plainPassword = $plainPassword -replace '\s', ''
    if ([string]::IsNullOrWhiteSpace($plainPassword)) {
        throw 'The SMTP password cannot be empty.'
    }
    if ($plainPassword.Length -ne 16) {
        throw "The Google app password must contain exactly 16 characters after spaces are removed (received $($plainPassword.Length))."
    }

    Set-ProcessEnvironment @{
        SMTP_HOST = 'smtp.gmail.com'
        SMTP_PORT = '587'
        SMTP_USERNAME = $GmailAddress
        SMTP_PASSWORD = $plainPassword
        SMTP_FROM = $GmailAddress
        SMTP_STARTTLS = '1'
    }

    Write-Host '[configure temporary recipient]'
    $sql = @"
DO `$`$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM devices WHERE id = '$DeviceId' AND owner_user_id = '$OwnerUserId'
    ) THEN
        RAISE EXCEPTION 'The requested HOME device does not belong to the requested owner.';
    END IF;
END
`$`$;

INSERT INTO recipients (
    id, name, role, email, email_enabled, sms, push, ars, enabled,
    facility_id, owner_user_id, resident_id
)
VALUES (
    '$recipientId', 'Temporary external email E2E', 'FAMILY', '$RecipientEmail',
    true, false, false, false, true, NULL, '$OwnerUserId', NULL
);
"@
    $sql | & docker exec -i $postgresContainer psql -U wifiguard -d wifiguard -v ON_ERROR_STOP=1
    if ($LASTEXITCODE -ne 0) {
        throw "Recipient update failed with exit code $LASTEXITCODE"
    }

    Write-Host '[start API with one-time Gmail SMTP credentials]'
    Push-Location $composeDir
    try {
        Invoke-Checked -Description 'API recreation for Gmail SMTP' -Command {
            & docker compose -f $composeFile --env-file $envFile up -d --no-deps --force-recreate api
        }
    }
    finally {
        Pop-Location
    }
    $null = Wait-Api
    Wait-KafkaIngestGroup

    Write-Host '[direct SMTP adapter test]'
    $python = @'
import json
import os
from wifiguard_notify import EmailNotifier

notifier = EmailNotifier(
    "external-smtp-test",
    os.environ["EXTERNAL_TEST_RECIPIENT"],
    display_name="External SMTP test",
)
ok = notifier.send_test_now()
print(json.dumps({"ok": ok, "status": notifier.status()}, ensure_ascii=False))
raise SystemExit(0 if ok else 2)
'@
    # Passing multiline Python directly through PowerShell -> docker.exe can
    # strip embedded quotes. Base64 keeps the source as one argument and does
    # not contain the SMTP password.
    $pythonBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($python))
    $pythonLauncher = "import base64;exec(base64.b64decode('$pythonBase64'))"
    & docker exec -e "EXTERNAL_TEST_RECIPIENT=$RecipientEmail" $apiContainer python -c $pythonLauncher
    if ($LASTEXITCODE -ne 0) {
        throw 'Gmail SMTP rejected the adapter test. Generate a fresh Google app password for the supplied Gmail account; do not use the normal account password.'
    }

    Write-Host '[synthetic fall -> Kafka -> ingest -> Gmail SMTP -> recipient inbox]'
    $now = [DateTimeOffset]::UtcNow
    $payload = [ordered]@{
        kind = 'inference'
        schema_version = 1
        tenant_id = $tenantId
        device_id = $deviceId
        ts = $now.ToString('o')
        seq = $now.ToUnixTimeMilliseconds()
        proba_fall = 0.99
        threshold = 0.468
        postprocess = 'segmentation_b'
        decision = $true
        inferred_at = $now.ToString('o')
        feature_ms = 0.0
        infer_ms = 0.0
        model_version = $syntheticModelVersion
        scale_cache_hit = $true
    } | ConvertTo-Json -Compress

    $payload | & docker exec -i wifiguard-dev-kafka-1 /opt/kafka/bin/kafka-console-producer.sh `
        --bootstrap-server localhost:9092 `
        --topic csi-inference-result
    if ($LASTEXITCODE -ne 0) {
        throw "Kafka synthetic fall injection failed with exit code $LASTEXITCODE"
    }

    $notification = $null
    $emailStatus = @()
    $health = $null
    $deliveryDeadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
    do {
        Start-Sleep -Seconds 2
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5
        $notification = $health.ingest.fall_notification
        $emailStatus = @(
            $notification.notifiers |
                Where-Object { $_.channel -eq 'email' -and $_.target -eq $RecipientEmail }
        )
    } while (
        [DateTimeOffset]::UtcNow -lt $deliveryDeadline -and
        (-not $emailStatus.Count -or ($emailStatus[0].sent_count -lt 1 -and $emailStatus[0].failed_count -lt 1))
    )
    [pscustomobject]@{
        recipient = $RecipientEmail
        fall_sink_written = $health.ingest.fall_sink.written
        notification_queued = $notification.queued
        notification_errors = $notification.errors
        email_sent_count = if ($emailStatus.Count) { $emailStatus[0].sent_count } else { $null }
        email_failed_count = if ($emailStatus.Count) { $emailStatus[0].failed_count } else { $null }
        email_last_error = if ($emailStatus.Count) { $emailStatus[0].last_error } else { 'email notifier not found' }
    } | Format-List

    if (-not $emailStatus.Count -or $emailStatus[0].sent_count -lt 1 -or $emailStatus[0].failed_count -ne 0) {
        throw 'The fall pipeline did not report a successful external email send.'
    }

    Write-Host 'EXTERNAL_EMAIL_E2E_SMTP_ACCEPTED'
}
finally {
    if ($null -ne $plainPassword) {
        $plainPassword = $null
    }
    if ($passwordPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr)
    }
    $securePassword.Dispose()

    Restore-ProcessEnvironment $previousEnvironment

    Write-Host '[restore API to local .env SMTP configuration]'
    Push-Location $composeDir
    try {
        & docker compose -f $composeFile --env-file $envFile up -d --no-deps --force-recreate api
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Automatic API restore failed. Recreate the API manually before continuing.'
        }
        else {
            $null = Wait-Api
            Write-Host 'LOCAL_SMTP_CONFIGURATION_RESTORED'
        }
    }
    finally {
        Pop-Location
    }

    $cleanupSql = @"
DELETE FROM fall_events
WHERE model_version = '$syntheticModelVersion';

DELETE FROM recipients
WHERE id = '$recipientId';
"@
    try {
        $cleanupSql | & docker exec -i $postgresContainer psql -U wifiguard -d wifiguard -v ON_ERROR_STOP=1 | Out-Host
    }
    catch {
        Write-Warning "Temporary external-email E2E rows could not be removed automatically: $($_.Exception.Message)"
    }
}

Write-Host ''
Write-Host "Gmail SMTP accepted both the adapter test and the synthetic fall alert for $RecipientEmail."
Write-Host 'Check the recipient inbox and spam folder. SMTP acceptance alone does not prove inbox display.'
