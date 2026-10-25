# One-command uninstaller for scrapper-legislatie-just-cli on Windows.
#
#   irm https://raw.githubusercontent.com/ClimenteA/scrapper-legislatie-just-cli/main/uninstall.ps1 | iex
#
# Removes what install.ps1 installed: the `uv tool` environment with the CLI and
# the MCP server, plus monolith.exe in the tool bin directory. `uv` itself and
# the downloaded laws stay unless you use --purge-data.
#
# Options (download the file first, or use the environment variables with irm):
#
#   $env:SCRAPPER_KEEP_MONOLITH = '1'; irm .../uninstall.ps1 | iex
#
#   --keep-monolith   keep monolith.exe in the tool bin directory
#   --purge-data      also delete the local data folder (legislatie-descarcata)
#   --dry-run         print what would be removed, change nothing
#   --help
#
# Environment: SCRAPPER_DATA_DIR, SCRAPPER_KEEP_MONOLITH=1

$ErrorActionPreference = 'Stop'

$Pkg = 'scrapper-legislatie-just-cli'
$KeepMonolith = $env:SCRAPPER_KEEP_MONOLITH -eq '1'
$PurgeData = $false
$DryRun = $false

function Write-Step($Message) { Write-Host "`n==> $Message" }
function Write-Info($Message) { Write-Host "    $Message" }
function Write-Warn($Message) { Write-Host "warn: $Message" -ForegroundColor Yellow }

function Show-Usage {
    Write-Host @"
Uninstall $Pkg

  irm <url>/uninstall.ps1 | iex

Options:
  --keep-monolith   keep monolith.exe in the tool bin directory
  --purge-data      also delete the local data folder (legislatie-descarcata)
  --dry-run         print what would be removed, change nothing
  --help            show this help

Environment: SCRAPPER_DATA_DIR, SCRAPPER_KEEP_MONOLITH=1

Removes the CLI and the MCP server installed with `uv tool`. `uv` itself is
not removed, and the downloaded laws stay unless --purge-data is given.
"@
}

for ($i = 0; $i -lt $args.Count; $i++) {
    $arg = [string]$args[$i]
    switch -Regex ($arg) {
        '^(-k|--keep-monolith)$' { $KeepMonolith = $true }
        '^(-d|--purge-data)$'    { $PurgeData = $true }
        '^(-n|--dry-run)$'       { $DryRun = $true }
        '^(-h|--help)$'          { Show-Usage; return }
        default                  { throw "Unknown option: $arg (try --help)" }
    }
}

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
    # PowerShell 7 already negotiates TLS by itself.
}

function Update-Path {
    $bin = Join-Path $env:USERPROFILE '.local\bin'
    if ((Test-Path $bin) -and ($env:Path -notlike "*$bin*")) {
        $env:Path = "$bin;$env:Path"
    }
}

function Get-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

# --- uv and paths -----------------------------------------------------------

Write-Step 'Checking uv'
Update-Path
$uv = Get-Uv
if ($uv) { Write-Info "uv: $(& uv --version)" }

$toolBinDir = $null
if ($uv) {
    $toolBinDir = (& uv tool dir --bin 2>$null)
}
if (-not $toolBinDir) { $toolBinDir = Join-Path $env:USERPROFILE '.local\bin' }

# --- package ----------------------------------------------------------------

Write-Step "Removing $Pkg"
$listed = ''
if ($uv) { $listed = (& uv tool list 2>$null) -join "`n" }

if (-not $uv) {
    Write-Warn 'uv is not installed, so there is no uv tool environment to remove.'
    Write-Info "If a $Pkg command still answers, it was installed another way (pip, pipx, a checkout)."
} elseif ($listed -notmatch "(?m)^$([regex]::Escape($Pkg))\s") {
    Write-Info "$Pkg is not installed in uv; nothing to remove."
} elseif ($DryRun) {
    Write-Info "would run: uv tool uninstall $Pkg"
} else {
    & uv tool uninstall $Pkg
    if ($LASTEXITCODE -ne 0) {
        throw "uv tool uninstall failed (exit $LASTEXITCODE)."
    }
    Write-Info "removed $Pkg (CLI and MCP server)"
}

# --- monolith ---------------------------------------------------------------

Write-Step 'Checking monolith'
if ($KeepMonolith) {
    Write-Info 'keeping monolith (--keep-monolith)'
} else {
    $dest = Join-Path $toolBinDir 'monolith.exe'
    if (Test-Path $dest) {
        if ($DryRun) {
            Write-Info "would remove: $dest"
        } else {
            Remove-Item -Force $dest
            Write-Info "removed: $dest"
        }
    } else {
        Write-Info "no monolith.exe in $toolBinDir"
    }
    Write-Info 'monolith installed with winget or scoop is left alone.'
}

# --- data -------------------------------------------------------------------

if ($PurgeData) {
    Write-Step 'Removing local data'
    $dataDir = if ($env:SCRAPPER_DATA_DIR) { $env:SCRAPPER_DATA_DIR } else { '.\legislatie-descarcata' }
    if (Test-Path $dataDir) {
        if ($DryRun) {
            Write-Info "would delete: $dataDir"
        } else {
            Remove-Item -Recurse -Force $dataDir
            Write-Info "deleted: $dataDir"
        }
    } else {
        Write-Info "no data folder at $dataDir"
    }
} else {
    $shown = if ($env:SCRAPPER_DATA_DIR) { $env:SCRAPPER_DATA_DIR } else { '.\legislatie-descarcata' }
    Write-Info "the downloaded laws stay in $shown (use --purge-data to delete them)"
}

# --- done -------------------------------------------------------------------

Write-Step 'Done'
if ($DryRun) {
    Write-Info 'dry run: nothing was changed.'
}
$uvNote = if ($uv) { "still installed in $env:USERPROFILE\.local\bin (it may serve other tools)" } else { 'not found on this machine' }
Write-Host @"

Uninstalled. What was left in place:

    uv                 $uvNote
    data folder        $(if ($env:SCRAPPER_DATA_DIR) { $env:SCRAPPER_DATA_DIR } else { '.\legislatie-descarcata' })
    PATH entries       remove $toolBinDir from your user PATH by hand if you want

To drop uv as well:  uv self uninstall
"@
