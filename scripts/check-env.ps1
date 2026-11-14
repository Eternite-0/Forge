param(
    [switch]$NativeSmoke
)

$ErrorActionPreference = 'Stop'
$failureCount = 0
$toolChecks = @(
    @{ Name = 'Git'; Command = 'git'; Arguments = @('--version') },
    @{ Name = 'Python'; Command = 'python'; Arguments = @('--version') },
    @{ Name = 'pip'; Command = 'python'; Arguments = @('-m', 'pip', '--version') },
    @{ Name = 'Node.js'; Command = 'node'; Arguments = @('--version') },
    @{ Name = 'npm'; Command = 'npm.cmd'; Arguments = @('--version') },
    @{ Name = 'pnpm'; Command = 'pnpm.cmd'; Arguments = @('--version') },
    @{ Name = 'Rust'; Command = 'rustc'; Arguments = @('--version') },
    @{ Name = 'Cargo'; Command = 'cargo'; Arguments = @('--version') }
)

foreach ($check in $toolChecks) {
    $command = Get-Command $check.Command -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $command) {
        Write-Host ('MISSING {0}' -f $check.Name)
        $failureCount++
        continue
    }
    $toolArguments = $check.Arguments
    $versionOutput = & $command.Source @toolArguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host ('FAILED {0}: {1}' -f $check.Name, ($versionOutput -join ' '))
        $failureCount++
    } else {
        Write-Host ('OK {0}: {1}' -f $check.Name, ($versionOutput -join ' '))
    }
}

$vswherePath = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$compilerPaths = @()
if (Test-Path -LiteralPath $vswherePath) {
    $compilerPaths = @(& $vswherePath -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -find 'VC\Tools\MSVC\**\bin\Hostx64\x64\cl.exe')
}
if ($compilerPaths.Count -gt 0) {
    Write-Host ('OK MSVC: {0}' -f $compilerPaths[0])
} else {
    Write-Host 'MISSING Visual Studio C++ x64 build tools'
    $failureCount++
}

$sdkRoot = Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows Kits\Installed Roots' -Name KitsRoot10 -ErrorAction SilentlyContinue
$sdkVersions = @()
if ($sdkRoot) {
    $sdkVersions = @(Get-ChildItem -LiteralPath (Join-Path $sdkRoot 'Include') -Directory -ErrorAction SilentlyContinue | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName 'um\Windows.h')) -and
        (Test-Path -LiteralPath (Join-Path $sdkRoot ('Lib\{0}\um\x64\kernel32.lib' -f $_.Name))) -and
        (Test-Path -LiteralPath (Join-Path $sdkRoot ('Lib\{0}\ucrt\x64\ucrt.lib' -f $_.Name)))
    } | Sort-Object Name -Descending)
}
if ($sdkVersions.Count -gt 0) {
    Write-Host ('OK Windows SDK: {0}' -f $sdkVersions[0].Name)
} else {
    Write-Host 'MISSING Windows SDK headers or x64 libraries'
    $failureCount++
}

$webViewRoots = @(
    "${env:ProgramFiles(x86)}\Microsoft\EdgeWebView\Application",
    "$env:ProgramFiles\Microsoft\EdgeWebView\Application",
    "$env:LOCALAPPDATA\Microsoft\EdgeWebView\Application"
)
$webViewVersions = @(foreach ($webViewRoot in $webViewRoots) {
    if (Test-Path -LiteralPath $webViewRoot) {
        Get-ChildItem -LiteralPath $webViewRoot -Directory | Where-Object {
            $_.Name -match '^\d+\.\d+\.\d+\.\d+$' -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'msedgewebview2.exe'))
        }
    }
})
if ($webViewVersions.Count -gt 0) {
    $latestWebView = $webViewVersions | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
    Write-Host ('OK WebView2: {0}' -f $latestWebView.Name)
} else {
    Write-Host 'MISSING WebView2 Runtime'
    $failureCount++
}

if ($NativeSmoke) {
    if ($failureCount -gt 0) {
        Write-Host 'Native compilation skipped: resolve missing prerequisites first.'
    } else {
        $nativeDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\.local\native-check'))
        New-Item -ItemType Directory -Path $nativeDirectory -Force | Out-Null
        $sourcePath = Join-Path $nativeDirectory 'forge_native_check.rs'
        $executablePath = Join-Path $nativeDirectory 'forge_native_check.exe'
        [System.IO.File]::WriteAllText($sourcePath, 'fn main() { println!("Forge native toolchain OK"); }', [System.Text.UTF8Encoding]::new($false))
        & rustc --edition=2021 --target x86_64-pc-windows-msvc $sourcePath -o $executablePath
        if ($LASTEXITCODE -ne 0) {
            throw 'Native Rust compilation failed.'
        }
        & $executablePath
        if ($LASTEXITCODE -ne 0) {
            throw 'Native Rust executable failed.'
        }
    }
}

if ($failureCount -gt 0) {
    Write-Host ('Environment check failed: {0} prerequisite(s) unavailable.' -f $failureCount)
    exit 1
}
Write-Host 'Environment check passed.'
