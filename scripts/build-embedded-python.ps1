param(
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$BuildPython = 'python',
    [string]$WheelDirectory = '',
    [switch]$Offline
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputPath) { throw 'Output directory must not already exist.' }
$expectedSha256 = '9E35319BFFEDAB8B36AD44D8F377CB4F1733BCEC257A0B6D04D94F01486DDB38'
$sourceUri = 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip'
$cachePath = Join-Path $projectRoot '.local/phase1/milestone7/native/downloads'
New-Item -ItemType Directory -Force -Path $cachePath | Out-Null
$archive = Join-Path $cachePath 'python-3.13.17-embed-amd64.zip'
if (-not (Test-Path -LiteralPath $archive)) {
    if ($Offline) { throw 'Offline build requires the cached official Python archive.' }
    Invoke-WebRequest -Uri $sourceUri -OutFile $archive
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash -ne $expectedSha256) { throw 'Official embedded Python archive hash mismatch.' }
if (-not $WheelDirectory) { $WheelDirectory = Join-Path $cachePath 'wheels' }
New-Item -ItemType Directory -Force -Path $WheelDirectory | Out-Null
$lock = Join-Path $projectRoot 'backend/requirements-lock.txt'
if (-not $Offline) {
    & $BuildPython -m pip download --only-binary=:all: --require-hashes -r $lock --dest $WheelDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Hash-locked wheel download failed.' }
}
New-Item -ItemType Directory -Path $outputPath | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $outputPath
$site = Join-Path $outputPath 'site-packages'
& $BuildPython -m pip install --no-index --find-links $WheelDirectory --only-binary=:all: --require-hashes --no-compile --target $site -r $lock
if ($LASTEXITCODE -ne 0) { throw 'Offline hash-locked wheel install failed.' }
$sourceRoot = Join-Path $projectRoot 'backend/src'
$sourceManifest = @()
foreach ($package in @('forge_api', 'forge_core')) {
    $packageRoot = Join-Path $sourceRoot $package
    foreach ($file in (Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Where-Object { $_.Extension -in @('.py', '.json') -and $_.FullName -notmatch '[\\/]__pycache__[\\/]' })) {
        $relative = [IO.Path]::GetRelativePath($sourceRoot, $file.FullName)
        $destination = Join-Path $site $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination
        $copiedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash -ne $copiedHash) { throw 'Source changed while runtime was being built.' }
        $sourceManifest += @{path=$relative.Replace('\','/'); sha256=$copiedHash}
    }
}
Set-Content -LiteralPath (Join-Path $outputPath 'python313._pth') -Encoding ascii -Value @('python313.zip', '.', 'site-packages', 'import site')
$interpreter = Join-Path $outputPath 'python.exe'
& $interpreter -B -I -c 'import sys,fastapi,uvicorn,pydantic,httpx,jsonschema,forge_core,forge_api; assert sys.version_info[:3] == (3,13,17); print(sys.version)'
if ($LASTEXITCODE -ne 0) { throw 'Embedded isolated runtime import smoke failed.' }
$isolationProbe = @'
import json, os, subprocess, sys
from pathlib import Path
from forge_core.evidence import _VALIDATOR
from forge_core import numeric_harness

assert sys.flags.isolated == 1
good = subprocess.run([sys.executable, '-B', '-I', '-c', _VALIDATOR], input=b'{"ok":true}', capture_output=True, timeout=10)
bad = subprocess.run([sys.executable, '-B', '-I', '-c', _VALIDATOR], input=b'{bad}', capture_output=True, timeout=10)
assert good.returncode == 0 and bad.returncode != 0
harness = Path(numeric_harness.__file__).resolve()
proc = subprocess.run([sys.executable, '-B', '-I', str(harness)], input=b'{"source":"result = a + b","inputs":{"a":2,"b":3}}', capture_output=True, timeout=10)
assert proc.returncode == 0 and json.loads(proc.stdout)['result'] == 5
candidate = 'if eligible_a == 1:\n    if eligible_b == 1:\n        if cost_a <= cost_b:\n            result = 0\n        else:\n            result = 1\n    else:\n        result = 0\nelse:\n    if eligible_b == 1:\n        result = 1\n    else:\n        result = -1\n'
semantic = subprocess.run([sys.executable, '-B', '-I', '-m', 'forge_core.semantic_suite'], input=json.dumps({'source':candidate,'suite_id':'scheduler-v1'}).encode(), capture_output=True, timeout=10)
assert semantic.returncode == 0 and json.loads(semantic.stdout)['passed'] is True
print('isolated verifier, numeric harness and semantic suite passed')
'@
& $interpreter -B -I -c $isolationProbe
if ($LASTEXITCODE -ne 0) { throw 'Embedded verifier or numeric harness smoke failed.' }
$runtimeFiles = @(Get-ChildItem -LiteralPath $outputPath -Recurse -File | ForEach-Object { @{path=[IO.Path]::GetRelativePath($outputPath, $_.FullName).Replace('\','/'); sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash} })
@{python='3.13.17'; archive_url=$sourceUri; archive_sha256=$expectedSha256; spdx_url="$sourceUri.spdx.json"; sigstore_url="$sourceUri.sigstore"; requirements_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath $lock).Hash; source_files=$sourceManifest; runtime_files=$runtimeFiles} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $outputPath 'forge-runtime-manifest.json') -Encoding utf8
