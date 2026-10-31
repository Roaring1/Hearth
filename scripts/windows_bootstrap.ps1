#Requires -Version 5.1
<#
.SYNOPSIS
  Bootstrap or verify Hearth's experimental Windows runtime in MSYS2 UCRT64.

.DESCRIPTION
  Uses the MSYS2 UCRT64 Python and a venv that inherits pacman-managed GTK/COM
  packages. Pycaw is installed from the Hearth wheel's Windows-only dependency.
  This script does not start services, open the mixer window, or touch audio
  endpoints. Missing MSYS2 packages are reported by default; installation
  requires the explicit -InstallRuntimePackages switch.
#>
[CmdletBinding()]
param(
    [string]$MsysRoot = 'C:\msys64',
    [string]$VenvPath = '',
    [string]$WheelPath = '',
    [switch]$InstallRuntimePackages,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

if ($CheckOnly -and $InstallRuntimePackages) {
    throw 'Use either -CheckOnly or -InstallRuntimePackages, not both.'
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($VenvPath)) {
    $VenvPath = Join-Path $RepoRoot '.venv-win'
}
if (-not [System.IO.Path]::IsPathRooted($VenvPath)) {
    $VenvPath = Join-Path $RepoRoot $VenvPath
}
$VenvPath = [System.IO.Path]::GetFullPath($VenvPath)

$UcrtBin = Join-Path $MsysRoot 'ucrt64\bin'
$MsysBin = Join-Path $MsysRoot 'usr\bin'
$UcrtPython = Join-Path $UcrtBin 'python.exe'
$Pacman = Join-Path $MsysBin 'pacman.exe'

# Make the UCRT64 runtime available even when this script is launched from
# ordinary PowerShell rather than an initialized MSYS2 terminal. PyGObject
# uses PYGI_DLL_PATH to locate its native GTK/GLib dependencies.
$env:MSYSTEM = 'UCRT64'
$env:PATH = "$UcrtBin;$MsysBin;$env:PATH"
$env:PYGI_DLL_PATH = "$UcrtBin;$MsysBin"
$VenvPython = Join-Path $VenvPath 'bin\python.exe'

if (-not (Test-Path -LiteralPath $UcrtPython -PathType Leaf)) {
    throw "MSYS2 UCRT64 Python was not found: $UcrtPython"
}
if (-not (Test-Path -LiteralPath $Pacman -PathType Leaf)) {
    throw "MSYS2 pacman was not found: $Pacman"
}

$RequiredPackages = @(
    'mingw-w64-ucrt-x86_64-python',
    'mingw-w64-ucrt-x86_64-python-pip',
    'mingw-w64-ucrt-x86_64-python-gobject',
    'mingw-w64-ucrt-x86_64-gtk4',
    'mingw-w64-ucrt-x86_64-glib2',
    'mingw-w64-ucrt-x86_64-gdk-pixbuf2',
    'mingw-w64-ucrt-x86_64-gobject-introspection-runtime',
    'mingw-w64-ucrt-x86_64-cairo',
    'mingw-w64-ucrt-x86_64-python-cairo',
    'mingw-w64-ucrt-x86_64-python-comtypes',
    'mingw-w64-ucrt-x86_64-python-psutil'
)

$InstalledPackages = @(& $Pacman -Qq)
if ($LASTEXITCODE -ne 0) {
    throw 'Could not query the installed MSYS2 package set with pacman -Qq.'
}
$InstalledSet = @{}
foreach ($Package in $InstalledPackages) {
    $InstalledSet[$Package] = $true
}
$MissingPackages = @($RequiredPackages | Where-Object { -not $InstalledSet.ContainsKey($_) })

if ($MissingPackages.Count -gt 0) {
    if (-not $InstallRuntimePackages) {
        $List = $MissingPackages -join ', '
        throw "Missing MSYS2 runtime packages: $List. Review docs/WINDOWS_PACKAGING.md and rerun with -InstallRuntimePackages to install them."
    }

    Write-Host "Installing missing UCRT64 packages: $($MissingPackages -join ', ')"
    & $Pacman -S --needed --noconfirm @MissingPackages
    if ($LASTEXITCODE -ne 0) {
        throw 'pacman failed while installing the required UCRT64 packages.'
    }

    $InstalledPackages = @(& $Pacman -Qq)
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not re-query MSYS2 packages after installation.'
    }
    $InstalledSet = @{}
    foreach ($Package in $InstalledPackages) {
        $InstalledSet[$Package] = $true
    }
    $MissingPackages = @($RequiredPackages | Where-Object { -not $InstalledSet.ContainsKey($_) })
    if ($MissingPackages.Count -gt 0) {
        throw "MSYS2 packages remain missing after pacman: $($MissingPackages -join ', ')"
    }
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    if ($CheckOnly) {
        throw "The Hearth UCRT64 venv is missing: $VenvPath"
    }
    Write-Host "Creating venv with inherited UCRT64 site packages: $VenvPath"
    & $UcrtPython -m venv --system-site-packages $VenvPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create the MSYS2 UCRT64 venv.'
    }
}

$VenvConfig = Join-Path $VenvPath 'pyvenv.cfg'
if (-not (Test-Path -LiteralPath $VenvConfig -PathType Leaf)) {
    throw "The venv configuration file is missing: $VenvConfig"
}
$Configuration = Get-Content -LiteralPath $VenvConfig -Raw
if ($Configuration -notmatch '(?im)^\s*include-system-site-packages\s*=\s*true\s*$') {
    throw "The venv must inherit MSYS2 site packages. Recreate it with $UcrtPython -m venv --system-site-packages $VenvPath"
}
$HomeMatch = [regex]::Match($Configuration, '(?im)^\s*home\s*=\s*(.+?)\s*$')
if (-not $HomeMatch.Success) {
    throw "Could not identify the base interpreter in $VenvConfig"
}
$ConfiguredHome = [System.IO.Path]::GetFullPath($HomeMatch.Groups[1].Value.Trim())
$ExpectedHome = [System.IO.Path]::GetFullPath($UcrtBin)
if (-not $ConfiguredHome.Equals($ExpectedHome, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The venv uses '$ConfiguredHome', expected MSYS2 UCRT64 '$ExpectedHome'."
}
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "The venv Python executable is missing: $VenvPython"
}

if (-not $CheckOnly) {
    if ([string]::IsNullOrWhiteSpace($WheelPath)) {
        $Dist = Join-Path $RepoRoot 'dist'
        $Candidate = Get-ChildItem -LiteralPath $Dist -Filter 'hearth-*.whl' -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -eq $Candidate) {
            throw "No Hearth wheel was found in $Dist. Build one with the MSYS2 UCRT64 Python first, or pass -WheelPath."
        }
        $WheelPath = $Candidate.FullName
    } elseif (-not [System.IO.Path]::IsPathRooted($WheelPath)) {
        $WheelPath = Join-Path $RepoRoot $WheelPath
    }
    $WheelPath = [System.IO.Path]::GetFullPath($WheelPath)
    if (-not (Test-Path -LiteralPath $WheelPath -PathType Leaf)) {
        throw "The Hearth wheel does not exist: $WheelPath"
    }

    Write-Host "Installing Hearth wheel: $WheelPath"
    & $VenvPython -m pip install --upgrade $WheelPath
    if ($LASTEXITCODE -ne 0) {
        throw 'pip failed while installing the Hearth wheel and its marked dependencies.'
    }
} else {
    Write-Host 'CheckOnly requested: skipping venv creation and pip installation.'
    # The development venv is intentionally not an editable install. Import
    # the checkout's source tree for this read-only probe; installed-wheel mode
    # leaves PYTHONPATH untouched so it verifies the installed distribution.
    $SourceRoot = Join-Path $RepoRoot 'src'
    if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
        $env:PYTHONPATH = $SourceRoot
    } else {
        $env:PYTHONPATH = $SourceRoot + ';' + $env:PYTHONPATH
    }
}

$ProbeScript = Join-Path $RepoRoot 'scripts\windows_runtime_probe.py'
if (-not (Test-Path -LiteralPath $ProbeScript -PathType Leaf)) {
    throw "The Windows runtime import probe is missing: $ProbeScript"
}
& $VenvPython $ProbeScript
if ($LASTEXITCODE -ne 0) {
    throw 'The Hearth runtime import probe failed. No audio endpoint was opened or modified.'
}

Write-Host 'Hearth Windows runtime bootstrap/verification succeeded.'
Write-Host 'This checks imports only. It does not open GTK windows, start services, enumerate endpoints, or perform audio I/O.'
