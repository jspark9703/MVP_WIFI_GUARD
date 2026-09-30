[CmdletBinding()]
param(
    [string]$BackendDatabaseUrl = '',
    [switch]$SkipInstall,
    [switch]$SkipFrontendBuild,
    [switch]$SkipComposeConfig,
    [switch]$RunSoftwareE2E
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-CheckedStep {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Directory,
        [Parameter(Mandatory)][scriptblock]$Command
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

$raspberry = Join-Path $repoRoot 'WIFIGUARD-RASPBERRY'
$backend = Join-Path $repoRoot 'WIFIGUARD-BACKEND'
$legacyModel = Join-Path $backend 'services/csi-fall-pipeline'
$activeModel = Join-Path $backend 'services/csi-fall-segmentation'
$inference = Join-Path $backend 'services/inference'
$frontend = Join-Path $repoRoot 'WIFIGUARD-FRONTEND'

if (-not $SkipInstall) {
    Invoke-CheckedStep 'Raspberry dependencies' $raspberry {
        uv sync --extra mqtt --extra dev
    }
}
Invoke-CheckedStep 'Raspberry tests' $raspberry {
    uv run pytest -q
}

if (-not $SkipInstall) {
    Invoke-CheckedStep 'Backend dependencies' $backend {
        uv sync
    }
}
Invoke-CheckedStep 'Backend tests without database' $backend {
    uv run pytest -q packages/contracts/tests services/ingest/tests services/notification/tests
}

if ($BackendDatabaseUrl) {
    Invoke-CheckedStep 'Backend database integration tests' $backend {
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
    Write-Warning 'Backend API DB tests skipped. Pass -BackendDatabaseUrl with a disposable PostgreSQL test database.'
}

if (-not $SkipInstall) {
    Invoke-CheckedStep 'Live inference dependencies' $inference {
        uv sync --group dev
    }
}
Invoke-CheckedStep 'Live inference tests' $inference {
    uv run --group dev pytest -q
}

if (-not $SkipInstall) {
    Invoke-CheckedStep 'Active segmentation model dependencies' $activeModel {
        uv sync --extra test
    }
}
Invoke-CheckedStep 'Active segmentation model tests' $activeModel {
    uv run pytest -q
}

# The repository still ships the original model package. Keep its regression
# suite green even though production inference uses csi-fall-segmentation.
if (-not $SkipInstall) {
    Invoke-CheckedStep 'Legacy model dependencies' $legacyModel {
        uv sync --extra test
    }
}
Invoke-CheckedStep 'Legacy model regression tests' $legacyModel {
    uv run pytest -q
}

if (-not $SkipInstall) {
    Invoke-CheckedStep 'Frontend dependencies' $frontend {
        npm install --no-package-lock
    }
}
Invoke-CheckedStep 'Frontend tests' $frontend {
    npm test
}
Invoke-CheckedStep 'Frontend typecheck' $frontend {
    npm run typecheck
}
if (-not $SkipFrontendBuild) {
    Invoke-CheckedStep 'Frontend production build' $frontend {
        npm run build
    }
}
Invoke-CheckedStep 'Frontend lint' $frontend {
    npm run lint
}

if (-not $SkipComposeConfig) {
    Invoke-CheckedStep 'Docker Compose static configuration' (Join-Path $backend 'compose') {
        docker compose -f docker-compose.dev.yml --env-file .env.example config --quiet
    }
}

if ($RunSoftwareE2E) {
    try {
        Invoke-CheckedStep 'Model software E2E' $backend {
            uv run python tools/validate_model_software_e2e.py `
                --output artifacts/validation/software-e2e.json
        }
    }
    finally {
        & (Join-Path $PSScriptRoot 'clean-local-e2e-data.ps1') -Execute
    }
}

Invoke-CheckedStep 'Git whitespace check' $repoRoot {
    git -c core.whitespace=cr-at-eol diff --check
}

Write-Host "`nREPOSITORY_VALIDATION_PASS"
