# One-command installer for scrapper-legislatie-just-cli on Windows.
#
#   irm https://raw.githubusercontent.com/ClimenteA/scrapper-legislatie-just-cli/main/install.ps1 | iex
#
# Options (download the file first, or set the environment variables when using irm):
#
#   $env:SCRAPPER_REF = 'v0.1.0'; irm .../install.ps1 | iex
#
#   --ref REF        branch, tag or commit to install (default: main)
#   --upgrade        reinstall/refresh an existing installation
#   --python PY      Python version for the tool environment (default: 3.12)
#   --repo URL       install from a fork (git URL, or a local path)
#   --no-monolith    skip the automatic monolith binary install
#   --help
#
# Environment: SCRAPPER_REPO_URL, SCRAPPER_REF, SCRAPPER_PYTHON, SCRAPPER_NO_MONOLITH=1

$ErrorActionPreference = 'Stop'

$Pkg = 'scrapper-legislatie-just-cli'
$DefaultRepo = 'https://github.com/ClimenteA/scrapper-legislatie-just-cli.git'
$MonolithReleases = 'https://github.com/Y2Z/monolith/releases/latest/download'

$RepoUrl = if ($env:SCRAPPER_REPO_URL) { $env:SCRAPPER_REPO_URL } else { $DefaultRepo }
$Ref = if ($env:SCRAPPER_REF) { $env:SCRAPPER_REF } else { 'main' }
$PythonRequest = if ($env:SCRAPPER_PYTHON) { $env:SCRAPPER_PYTHON } else { '3.12' }
$Upgrade = $false
$WithMonolith = $env:SCRAPPER_NO_MONOLITH -ne '1'

function Write-Step($Message) { Write-Host "`n==> $Message" }
function Write-Info($Message) { Write-Host "    $Message" }
function Write-Warn($Message) { Write-Host "warn: $Message" -ForegroundColor Yellow }

function Show-Usage {
    Write-Host @"
Install $Pkg

  irm <url>/install.ps1 | iex

Options:
  --ref REF        branch, tag or commit to install (default: main)
  --upgrade        reinstall/refresh an existing installation
  --python PY      Python version for the tool environment (default: 3.12)
  --repo URL       install from a fork (git URL, or a local path)
  --no-monolith    skip the automatic monolith binary install
  --help           show this help
"@
}

for ($i = 0; $i -lt $args.Count; $i++) {
    $arg = [string]$args[$i]
    switch -Regex ($arg) {
        '^(-r|--ref)$'      { $i++; $Ref = [string]$args[$i] }
        '^--ref=(.+)$'      { $Ref = $Matches[1] }
        '^(-u|--upgrade)$'  { $Upgrade = $true }
        '^(-p|--python)$'   { $i++; $PythonRequest = [string]$args[$i] }
        '^--python=(.+)$'   { $PythonRequest = $Matches[1] }
        '^(--repo)$'        { $i++; $RepoUrl = [string]$args[$i] }
        '^--repo=(.+)$'     { $RepoUrl = $Matches[1] }
        '^(--no-monolith)$' { $WithMonolith = $false }
        '^(-h|--help)$'     { Show-Usage; return }
        default             { throw "Unknown option: $arg (try --help)" }
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

# --- uv ---------------------------------------------------------------------

Write-Step 'Checking uv'
Update-Path
$uv = Get-Uv
if (-not $uv) {
    Write-Info 'uv not found, installing it from astral.sh'
    irm https://astral.sh/uv/install.ps1 | iex
    Update-Path
    $uv = Get-Uv
}
if (-not $uv) {
    throw "uv was installed but is not on PATH. Open a new terminal and run this script again."
}
Write-Info "uv: $(& uv --version)"

# --- package ----------------------------------------------------------------

$spec = $RepoUrl
if ($spec -match '^git\+') {
    # already a uv style git URL
} elseif ($spec -match '^(https?|ssh|git)://' -or $spec -match '^git@') {
    $spec = "git+$spec"
}
if ($spec -like 'git+*') {
    if ($Ref) { $spec = "$spec@$Ref" }
}

if (($spec -like 'git+*') -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'git is required to install from a repository. Install Git for Windows and run this script again.'
}

Write-Step "Installing $Pkg"
Write-Info "source: $spec"
Write-Info "python: $PythonRequest"

$installArgs = @('tool', 'install', '--python', $PythonRequest)
$listed = (& uv tool list 2>$null) -join "`n"
if ($Upgrade -or ($listed -match "(?m)^$([regex]::Escape($Pkg))\s")) {
    $installArgs += '--force'
}
$installArgs += $spec

& uv @installArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host @"

error: installation failed.

If the repository is private, git needs credentials. Either make the
repository public, or install over SSH:

    `$env:SCRAPPER_REPO_URL = 'git@github.com:ClimenteA/scrapper-legislatie-just-cli.git'
    irm .../install.ps1 | iex

A proxy or a missing Python build toolchain can also break the build.
Run 'uv tool install $spec' by hand to see the full log.
"@ -ForegroundColor Red
    return
}

$toolBinDir = (& uv tool dir --bin 2>$null)
if (-not $toolBinDir) { $toolBinDir = Join-Path $env:USERPROFILE '.local\bin' }
$cli = Join-Path $toolBinDir "$Pkg.exe"
if (-not (Test-Path $cli)) {
    $plain = Join-Path $toolBinDir $Pkg
    if (Test-Path $plain) { $cli = $plain }
}
if (-not (Test-Path $cli)) {
    $cmd = Get-Command $Pkg -ErrorAction SilentlyContinue
    if ($cmd) { $cli = $cmd.Source }
}
if (-not (Test-Path $cli)) {
    throw "$Pkg was installed but its command was not found. Check $toolBinDir."
}
Write-Info "installed: $(& $cli --version) ($cli)"

if ($env:Path -notlike "*$toolBinDir*") {
    Write-Warn "$toolBinDir is not on PATH in this session."
    Write-Info "Add it once:  setx PATH `"$toolBinDir;%PATH%`"  (then open a new terminal)"
}

# --- monolith ---------------------------------------------------------------

function Show-MonolithHint {
    Write-Info 'winget install --id=Y2Z.Monolith -e     (or: scoop install main/monolith)'
    Write-Info 'or download Monolith.exe from https://github.com/Y2Z/monolith/releases'
}

if (Get-Command monolith -ErrorAction SilentlyContinue) {
    Write-Info "monolith: $(& monolith --version)"
} elseif (-not $WithMonolith) {
    Write-Warn 'monolith not found; the download step will not run until you install it.'
    Show-MonolithHint
} else {
    Write-Step "Installing monolith into $toolBinDir"
    $dest = Join-Path $toolBinDir 'monolith.exe'
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$MonolithReleases/monolith-cli.exe" -OutFile $dest
        Write-Info "monolith: $(& $dest --version)"
    } catch {
        if (Test-Path $dest) { Remove-Item -Force $dest }
        Write-Warn "Could not install monolith automatically: $($_.Exception.Message)"
        Show-MonolithHint
    }
}

# --- done -------------------------------------------------------------------

Write-Step 'Ready'
Write-Info "$Pkg --help"
Write-Info "$Pkg run-all --no-ocr        # download + index everything"
Write-Info "$Pkg search `"concediu de odihna`""
Write-Host @"

Data is stored in .\legislatie-descarcata (override with --data-dir or
SCRAPPER_DATA_DIR). The first run builds the index and takes a while.

MCP server for AI clients: scrapper-legislatie-just-mcp.exe, installed next to
the CLI in $toolBinDir. Configure your client with that command and set
SCRAPPER_DATA_DIR to the absolute path of legislatie-descarcata.
"@
