[CmdletBinding()]
param(
    [string]$BackendDatabaseUrl = "",
    [switch]$SkipInstall,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-CheckedStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )

    Write-Host "`n=== $Name ==="
    Push-Location -LiteralPath $Directory
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) {
            throw "$Name failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

$raspberry = Join-Path $repoRoot "WIFIGUARD-RASPBERRY"
$backend = Join-Path $repoRoot "WIFIGUARD-BACKEND"
$modelPipeline = Join-Path $backend "services/csi-fall-pipeline"
$inference = Join-Path $backend "services/inference"
$frontend = Join-Path $repoRoot "WIFIGUARD-FRONTEND"

if (-not $SkipInstall) {
    Invoke-CheckedStep "Raspberry dependencies" $raspberry {
        uv sync --extra mqtt --extra dev
    }
}
Invoke-CheckedStep "Raspberry tests" $raspberry {
    uv run pytest -q
}

if (-not $SkipInstall) {
    Invoke-CheckedStep "Backend dependencies" $backend {
        uv sync
    }
}
Invoke-CheckedStep "Backend tests without database" $backend {
    uv run pytest -q packages/contracts/tests services/ingest/tests services/notification/tests
}

if ($BackendDatabaseUrl) {
    Invoke-CheckedStep "Backend database integration tests" $backend {
        $previous = $env:DATABASE_URL_TEST
        try {
            $env:DATABASE_URL_TEST = $BackendDatabaseUrl
            uv run pytest -q services/api/tests
        }
        finally {
            $env:DATABASE_URL_TEST = $previous
        }
    }
}
else {
    Write-Warning "Backend API DB tests skipped. Pass -BackendDatabaseUrl with a disposable PostgreSQL test database."
}

if (-not $SkipInstall) {
    Invoke-CheckedStep "Live inference dependencies" $inference {
        uv sync --group dev
    }
}
Invoke-CheckedStep "Live inference tests" $inference {
    uv run --group dev pytest -q
}

if (-not $SkipInstall) {
    Invoke-CheckedStep "CSI model dependencies" $modelPipeline {
        uv sync --extra test
    }
}
Invoke-CheckedStep "CSI model tests" $modelPipeline {
    uv run pytest -q
}

if (-not $SkipInstall) {
    Invoke-CheckedStep "Frontend dependencies" $frontend {
        npm install --no-package-lock
    }
}
Invoke-CheckedStep "Frontend tests" $frontend {
    npm test
}
Invoke-CheckedStep "Frontend typecheck" $frontend {
    npm run typecheck
}
if (-not $SkipFrontendBuild) {
    Invoke-CheckedStep "Frontend production build" $frontend {
        npm run build
    }
}
Invoke-CheckedStep "Frontend lint" $frontend {
    npm run lint
}

Write-Host "`nREPOSITORY_VALIDATION_PASS"
