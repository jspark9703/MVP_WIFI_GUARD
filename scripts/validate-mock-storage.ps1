[CmdletBinding()]
param(
    [string]$NetworkName = "wifiguard-dev_default",
    [string]$KafkaBootstrap = "kafka:9092",
    [double]$ReplayDurationSeconds = 12
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot "WIFIGUARD-BACKEND"
$raspberry = Join-Path $repoRoot "WIFIGUARD-RASPBERRY"
$brokerName = "wifiguard-mock-mosquitto-$PID"

docker info --format "Docker server={{.ServerVersion}}" | Write-Host
if ($LASTEXITCODE -ne 0) { throw "Docker engine is not available" }
docker network inspect $NetworkName *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker network not found: $NetworkName" }

try {
    docker run --rm -d `
        --name $brokerName `
        --network $NetworkName `
        eclipse-mosquitto:2.0.21 `
        sh -c "printf 'listener 1883 0.0.0.0\nallow_anonymous true\npersistence false\n' > /tmp/mosquitto.conf && exec mosquitto -c /tmp/mosquitto.conf" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "temporary Mosquitto failed to start" }
    Start-Sleep -Seconds 2

    docker run --rm `
        --name "wifiguard-mock-validator-$PID" `
        --network $NetworkName `
        --mount "type=bind,src=$backend,dst=/workspace,readonly" `
        --mount "type=bind,src=$raspberry,dst=/WIFIGUARD-RASPBERRY,readonly" `
        --workdir /workspace/tools `
        --entrypoint sh `
        python:3.12-slim `
        -lc "pip install --quiet 'paho-mqtt==2.1.0' 'kafka-python>=2.1,<3' 'pydantic>=2.9,<3' 'numpy>=1.26,<3' 'scipy>=1.14,<2' 'pyserial>=3.5' && python validate_mock_mqtt_kafka.py --mqtt-host $brokerName --kafka-bootstrap $KafkaBootstrap --timeout 25 && python validate_replay_mqtt_kafka.py --mqtt-host $brokerName --kafka-bootstrap $KafkaBootstrap --duration $ReplayDurationSeconds --timeout 25"
    if ($LASTEXITCODE -ne 0) { throw "mock MQTT-to-Kafka validation failed" }
}
finally {
    docker stop --time 5 $brokerName *> $null
}

Write-Host "MOCK_STORAGE_VALIDATION_PASS"
