param([string]$WorkspaceRoot)

$ErrorActionPreference = "Stop"
if (-not $env:FORGE_CORE_SECRET) {
    throw "FORGE_CORE_SECRET is required in the process environment; see README for secure bootstrap."
}
$backendRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\backend"))
$backendPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
Push-Location $backendRoot
try {
    if (-not (Test-Path -LiteralPath $backendPython)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Creating the backend virtual environment failed" }
    }
    & $backendPython -m pip install --require-hashes -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw "Installing backend dependencies failed" }
    & $backendPython -m pip install --no-deps --no-build-isolation -e .
    if ($LASTEXITCODE -ne 0) { throw "Installing FORGE backend failed" }
    $launchArguments = @("-m", "forge_api")
    if ($PSBoundParameters.ContainsKey("WorkspaceRoot")) {
        $launchArguments += @("--workspace-root", $WorkspaceRoot)
    }
    & $backendPython @launchArguments
    if ($LASTEXITCODE -ne 0) { throw "FORGE Core exited with code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
