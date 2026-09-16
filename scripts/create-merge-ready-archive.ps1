[CmdletBinding()]
param(
    [string]$OutputPath = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$repoName = Split-Path -Leaf $repoRoot

if (-not $OutputPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputPath = Join-Path (Split-Path -Parent $repoRoot) "${repoName}_merge-ready_${stamp}.zip"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)

if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
    throw "Archive already exists: $OutputPath. Pass -Force to replace it."
}

$tracked = @(
    git -C $repoRoot -c core.quotepath=false ls-files --cached --others --exclude-standard -- .
)
if ($LASTEXITCODE -ne 0) {
    throw "git ls-files failed. Run this script inside a Git work tree."
}
$files = @(
    $tracked |
        ForEach-Object { $_.Trim('"') } |
        Where-Object { $_ -and (Test-Path -LiteralPath (Join-Path $repoRoot $_) -PathType Leaf) } |
        Sort-Object -Unique
)
if (-not $files) {
    throw "No merge-ready files found."
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path -LiteralPath $OutputPath) {
    Remove-Item -LiteralPath $OutputPath
}

$stream = [IO.File]::Open($OutputPath, [IO.FileMode]::CreateNew)
$archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($relative in $files) {
        $source = Join-Path $repoRoot $relative
        $entryName = ($repoName + "/" + ($relative -replace '\\', '/'))
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive,
            $source,
            $entryName,
            [IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}
finally {
    $archive.Dispose()
    $stream.Dispose()
}

$hash = Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256
Write-Host "archive=$OutputPath"
Write-Host "files=$($files.Count)"
Write-Host "bytes=$((Get-Item -LiteralPath $OutputPath).Length)"
Write-Host "sha256=$($hash.Hash)"
Write-Host "MERGE_READY_ARCHIVE_COMPLETE"
