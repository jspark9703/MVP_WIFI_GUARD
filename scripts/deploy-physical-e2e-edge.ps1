[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PiHost,

    [string]$PiUser = "wifiguard",

    [Parameter(Mandatory)]
    [string]$MqttHost,

    [int]$MqttPort = 1884,

    [string]$BundlePath
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$defaultBundle = Join-Path $repoRoot "WIFIGUARD-BACKEND\artifacts\validation\physical-e2e-live\wifiguard-physical-e2e-bundle.tar.gz"
$bundle = if ($BundlePath) { (Resolve-Path -LiteralPath $BundlePath).Path } else { $defaultBundle }
$remoteScript = Join-Path $repoRoot "scripts\deploy-physical-e2e-edge.sh"

if (-not (Test-Path $bundle)) {
    throw "Hardware E2E bundle does not exist: $bundle"
}

Write-Host "[transfer isolated hardware E2E bundle]"
$bundleSha256 = (Get-FileHash -LiteralPath $bundle -Algorithm SHA256).Hash.ToLowerInvariant()
& scp $bundle "${PiUser}@${PiHost}:/home/wifiguard/wifiguard-physical-e2e-bundle.tar.gz"
if ($LASTEXITCODE -ne 0) { throw "bundle scp failed: exit=$LASTEXITCODE" }

& scp $remoteScript "${PiUser}@${PiHost}:/home/wifiguard/deploy-physical-e2e-edge.sh"
if ($LASTEXITCODE -ne 0) { throw "deploy script scp failed: exit=$LASTEXITCODE" }

Write-Host "[remote preflight, build, and sender-off arm]"
& ssh -t "${PiUser}@${PiHost}" `
    "EXPECTED_SHA256='$bundleSha256' MQTT_HOST='$MqttHost' MQTT_PORT='$MqttPort' bash /home/wifiguard/deploy-physical-e2e-edge.sh"
if ($LASTEXITCODE -ne 0) { throw "remote deployment failed: exit=$LASTEXITCODE" }

Write-Host "PHYSICAL_E2E_EDGE_DEPLOYMENT_COMPLETE"
