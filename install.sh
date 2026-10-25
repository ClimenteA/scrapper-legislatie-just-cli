#!/bin/sh
# One-command installer for scrapper-legislatie-just-cli.
#
#   curl -LsSf https://raw.githubusercontent.com/ClimenteA/scrapper-legislatie-just-cli/main/install.sh | sh
#
# Works with any POSIX shell: Ubuntu/Debian, macOS, and Windows through
# Git Bash / MSYS2 / WSL. On native Windows PowerShell use install.ps1.
#
# When piping, pass options after `sh -s --`:
#
#   ... | sh -s -- --ref v0.1.0
#
# Options:
#   -r, --ref REF        branch, tag or commit to install (default: main)
#   -u, --upgrade        reinstall/refresh an existing installation
#   -p, --python PY      Python version for the tool environment (default: 3.12)
#       --repo URL       install from a fork (git URL, or a local path)
#       --no-monolith    skip the automatic monolith binary install
#   -h, --help           show this help
#
# Environment variables: SCRAPPER_REPO_URL, SCRAPPER_REF, SCRAPPER_PYTHON,
# SCRAPPER_NO_MONOLITH=1.

set -eu

PKG="scrapper-legislatie-just-cli"
DEFAULT_REPO="https://github.com/ClimenteA/scrapper-legislatie-just-cli.git"
MONOLITH_RELEASES="https://github.com/Y2Z/monolith/releases/latest/download"

repo_url="${SCRAPPER_REPO_URL:-$DEFAULT_REPO}"
ref="${SCRAPPER_REF:-main}"
python_req="${SCRAPPER_PYTHON:-3.12}"
upgrade=0
with_monolith=1
if [ "${SCRAPPER_NO_MONOLITH:-0}" = "1" ]; then
    with_monolith=0
fi

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Install scrapper-legislatie-just-cli

  curl -LsSf <url>/install.sh | sh

Options (when piping, pass them after `sh -s --`):
  -r, --ref REF        branch, tag or commit to install (default: main)
  -u, --upgrade        reinstall/refresh an existing installation
  -p, --python PY      Python version for the tool environment (default: 3.12)
      --repo URL       install from a fork (git URL, or a local path)
      --no-monolith    skip the automatic monolith binary install
  -h, --help           show this help

Environment variables: SCRAPPER_REPO_URL, SCRAPPER_REF, SCRAPPER_PYTHON,
SCRAPPER_NO_MONOLITH=1.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -r|--ref)
            [ $# -ge 2 ] || die "--ref needs a value"
            ref="$2"; shift 2 ;;
        --ref=*)   ref="${1#*=}"; shift ;;
        -u|--upgrade) upgrade=1; shift ;;
        -p|--python)
            [ $# -ge 2 ] || die "--python needs a value"
            python_req="$2"; shift 2 ;;
        --python=*) python_req="${1#*=}"; shift ;;
        --repo)
            [ $# -ge 2 ] || die "--repo needs a value"
            repo_url="$2"; shift 2 ;;
        --repo=*)  repo_url="${1#*=}"; shift ;;
        --no-monolith) with_monolith=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (try --help)" ;;
    esac
done

# --- platform ---------------------------------------------------------------

os="$(uname -s)"
arch="$(uname -m)"
case "$os" in
    Linux*)   platform=linux ;;
    Darwin*)  platform=macos ;;
    MINGW*|MSYS*|CYGWIN*) platform=windows ;;
    *) die "Unsupported operating system: $os" ;;
esac

if command -v curl >/dev/null 2>&1; then
    fetch() { curl -fLsS -o "$1" "$2"; }
    fetch_show() { curl -LsSf "$1"; }
elif command -v wget >/dev/null 2>&1; then
    fetch() { wget -qO "$1" "$2"; }
    fetch_show() { wget -qO- "$1"; }
else
    die "curl or wget is required."
fi

# --- helpers ----------------------------------------------------------------

refresh_path() {
    # uv writes ~/.local/bin/env on Unix; read it when present.
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    fi
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) PATH="$HOME/.local/bin:$PATH"; export PATH ;;
    esac
}

find_uv() {
    command -v uv 2>/dev/null || true
}

build_spec() {
    case "$repo_url" in
        git+*)        spec="$repo_url" ;;
        http://*|https://*|ssh://*|git://*) spec="git+$repo_url" ;;
        git@*)        spec="git+ssh://$(printf '%s' "$repo_url" | sed 's/^git@//; s/:/\//')" ;;
        *)            spec="$repo_url" ;;   # local path or pip-style requirement
    esac
    case "$spec" in
        git+*)
            if [ -n "$ref" ]; then
                spec="$spec@$ref"
            fi
            ;;
    esac
}

is_installed() {
    uv tool list 2>/dev/null | grep -q "^$PKG "
}

# --- uv ---------------------------------------------------------------------

step "Checking uv"
refresh_path
uv_bin="$(find_uv)"
if [ -z "$uv_bin" ]; then
    info "uv not found, installing it from astral.sh"
    fetch_show https://astral.sh/uv/install.sh | sh
    refresh_path
    uv_bin="$(find_uv)"
fi
[ -n "$uv_bin" ] || die "uv was installed but is not on PATH. Open a new terminal and run this script again."
info "uv: $("$uv_bin" --version)"

build_spec

case "$spec" in
    git+*)
        if ! command -v git >/dev/null 2>&1; then
            die "git is required to install from a repository. Install git and run this script again."
        fi
        ;;
esac

# --- package ----------------------------------------------------------------

step "Installing $PKG"
info "source: $spec"
info "python: $python_req"

set -- --python "$python_req"
if [ "$upgrade" = "1" ] || is_installed; then
    set -- "$@" --force
fi
set -- "$@" "$spec"

if ! "$uv_bin" tool install "$@"; then
    cat >&2 <<EOF

error: installation failed.

If the repository is private, git needs credentials. Either make the
repository public, or install over SSH:

    SCRAPPER_REPO_URL=git@github.com:ClimenteA/scrapper-legislatie-just-cli.git \\
      sh install.sh

A proxy or a missing Python build toolchain can also break the build;
re-run with -v (or run 'uv tool install $spec' by hand) to see the details.
EOF
    exit 1
fi

tool_bin_dir="$("$uv_bin" tool dir --bin 2>/dev/null || printf '%s' "$HOME/.local/bin")"
cli=""
for candidate in "$tool_bin_dir/$PKG" "$tool_bin_dir/$PKG.exe"; do
    if [ -x "$candidate" ]; then
        cli="$candidate"
        break
    fi
done
if [ -z "$cli" ]; then
    cli="$(command -v "$PKG" 2>/dev/null || true)"
fi
[ -n "$cli" ] || die "$PKG was installed but its command was not found. Check $tool_bin_dir."

installed_version="$("$cli" --version 2>/dev/null || printf 'unknown')"
info "installed: $installed_version ($cli)"

case ":$PATH:" in
    *":$tool_bin_dir:"*) ;;
    *)
        warn "$tool_bin_dir is not on PATH in this shell."
        info "Add it once, then open a new terminal:"
        info "  echo 'export PATH=\"$tool_bin_dir:\$PATH\"' >> ~/.profile"
        ;;
esac

# --- monolith ---------------------------------------------------------------

monolith_hint() {
    case "$platform" in
        macos)
            info "Install it with Homebrew:  brew install monolith"
            info "or MacPorts:               sudo port install monolith"
            ;;
        linux)
            info "Prebuilt binary:"
            info "  curl -fLsS $MONOLITH_RELEASES/monolith-cli-linux-x86_64 -o $1/monolith && chmod +x $1/monolith"
            info "Or a package:  snap install monolith  /  brew install monolith"
            ;;
        windows)
            info "winget install --id=Y2Z.Monolith -e   (or: scoop install main/monolith)"
            ;;
    esac
}

monolith_asset() {
    case "$platform" in
        windows) printf 'monolith-cli.exe' ;;
        linux)
            case "$arch" in
                x86_64|amd64)   printf 'monolith-cli-linux-x86_64' ;;
                aarch64|arm64)  printf 'monolith-cli-linux-aarch64' ;;
                armv7l|armhf)   printf 'monolith-cli-linux-armhf' ;;
                *)              printf '' ;;
            esac
            ;;
        *) printf '' ;;
    esac
}

install_monolith() {
    if command -v monolith >/dev/null 2>&1; then
        info "monolith: $(monolith --version 2>/dev/null || printf 'found')"
        return
    fi
    if [ "$with_monolith" != "1" ]; then
        warn "monolith not found; the download step will not run until you install it."
        monolith_hint "$tool_bin_dir"
        return
    fi

    asset="$(monolith_asset)"
    if [ -z "$asset" ]; then
        warn "No automatic monolith binary for $platform/$arch."
        monolith_hint "$tool_bin_dir"
        return
    fi

    dest_dir="$tool_bin_dir"
    if [ ! -w "$dest_dir" ] 2>/dev/null; then
        dest_dir="$HOME/.local/bin"
        mkdir -p "$dest_dir"
    fi
    dest="$dest_dir/monolith"
    if [ "$platform" = "windows" ]; then
        dest="$dest_dir/monolith.exe"
    fi

    step "Installing monolith into $dest_dir"
    if fetch "$dest" "$MONOLITH_RELEASES/$asset"; then
        chmod +x "$dest" 2>/dev/null || true
        if "$dest" --version >/dev/null 2>&1; then
            info "monolith: $("$dest" --version)"
        else
            rm -f "$dest"
            warn "The downloaded monolith binary did not run; install it manually."
            monolith_hint "$dest_dir"
        fi
    else
        rm -f "$dest"
        warn "Could not download monolith (no network access to GitHub releases?)."
        monolith_hint "$dest_dir"
    fi
}

install_monolith

# --- done -------------------------------------------------------------------

step "Ready"
info "$PKG --help"
info "$PKG run-all --no-ocr        # download + index everything"
info "$PKG search \"concediu de odihna\""
cat <<EOF

Data is stored in ./legislatie-descarcata (override with --data-dir or
SCRAPPER_DATA_DIR). First run builds the index; it takes a while.

MCP server for AI clients: scrapper-legislatie-just-mcp (installed next to
the CLI, in $tool_bin_dir). Configure your client with that command and set
SCRAPPER_DATA_DIR to the absolute path of legislatie-descarcata.
EOF
