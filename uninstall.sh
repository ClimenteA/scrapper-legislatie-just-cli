#!/bin/sh
# One-command uninstaller for scrapper-legislatie-just-cli.
#
#   curl -LsSf https://raw.githubusercontent.com/ClimenteA/scrapper-legislatie-just-cli/main/uninstall.sh | sh
#
# It removes what install.sh installed: the `uv tool` environment with the CLI
# and the MCP server, plus the monolith binary downloaded into the tool bin
# directory. `uv` itself and the downloaded laws are left alone unless asked.
#
# Works with any POSIX shell: Ubuntu/Debian, macOS, and Windows through
# Git Bash / MSYS2 / WSL. On native Windows PowerShell use uninstall.ps1.
#
# When piping, pass options after `sh -s --`:
#
#   ... | sh -s -- --purge-data
#
# Options:
#   -k, --keep-monolith   keep the monolith binary in the tool bin directory
#   -d, --purge-data      also delete the local data folder (legislatie-descarcata)
#   -n, --dry-run         print what would be removed, change nothing
#   -h, --help            show this help
#
# Environment variables: SCRAPPER_DATA_DIR (folder used by --purge-data),
# SCRAPPER_KEEP_MONOLITH=1.

set -eu

PKG="scrapper-legislatie-just-cli"

keep_monolith=0
purge_data=0
dry_run=0
if [ "${SCRAPPER_KEEP_MONOLITH:-0}" = "1" ]; then
    keep_monolith=1
fi

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Uninstall scrapper-legislatie-just-cli

  curl -LsSf <url>/uninstall.sh | sh

Options (when piping, pass them after `sh -s --`):
  -k, --keep-monolith   keep the monolith binary in the tool bin directory
  -d, --purge-data      also delete the local data folder (legislatie-descarcata)
  -n, --dry-run         print what would be removed, change nothing
  -h, --help            show this help

Environment variables: SCRAPPER_DATA_DIR, SCRAPPER_KEEP_MONOLITH=1.

Removes the CLI and the MCP server installed with `uv tool`. `uv` itself is
not removed, and the downloaded laws stay unless --purge-data is given.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -k|--keep-monolith) keep_monolith=1; shift ;;
        -d|--purge-data)    purge_data=1; shift ;;
        -n|--dry-run)       dry_run=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *) die "Unknown option: $1 (try --help)" ;;
    esac
done

# --- uv and paths -----------------------------------------------------------

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

step "Checking uv"
refresh_path
uv_bin="$(find_uv)"
if [ -n "$uv_bin" ]; then
    info "uv: $("$uv_bin" --version)"
fi

tool_bin_dir=""
if [ -n "$uv_bin" ]; then
    tool_bin_dir="$("$uv_bin" tool dir --bin 2>/dev/null || printf '%s' "$HOME/.local/bin")"
fi
[ -n "$tool_bin_dir" ] || tool_bin_dir="$HOME/.local/bin"

# --- package ----------------------------------------------------------------

step "Removing $PKG"
if [ -z "$uv_bin" ]; then
    warn "uv is not installed, so there is no uv tool environment to remove."
    info "If a $PKG command still answers, it was installed another way (pip, pipx, a checkout)."
elif ! "$uv_bin" tool list 2>/dev/null | grep -q "^$PKG "; then
    info "$PKG is not installed in uv; nothing to remove."
else
    if [ "$dry_run" = "1" ]; then
        info "would run: uv tool uninstall $PKG"
    else
        "$uv_bin" tool uninstall "$PKG"
        info "removed $PKG (CLI and MCP server)"
    fi
fi

# --- monolith ---------------------------------------------------------------

step "Checking monolith"
if [ "$keep_monolith" = "1" ]; then
    info "keeping monolith (--keep-monolith)"
else
    removed_monolith=0
    for candidate in "$tool_bin_dir/monolith" "$tool_bin_dir/monolith.exe"; do
        [ -e "$candidate" ] || continue
        if [ "$dry_run" = "1" ]; then
            info "would remove: $candidate"
        else
            rm -f "$candidate"
            info "removed: $candidate"
        fi
        removed_monolith=1
    done
    if [ "$removed_monolith" = "0" ]; then
        info "no monolith binary in $tool_bin_dir"
    fi
    info "monolith installed with a package manager (brew, snap, winget) is left alone."
fi

# --- data -------------------------------------------------------------------

if [ "$purge_data" = "1" ]; then
    step "Removing local data"
    data_dir="${SCRAPPER_DATA_DIR:-}"
    [ -n "$data_dir" ] || data_dir="./legislatie-descarcata"
    if [ -d "$data_dir" ]; then
        if [ "$dry_run" = "1" ]; then
            info "would delete: $data_dir"
        else
            rm -rf "$data_dir"
            info "deleted: $data_dir"
        fi
    else
        info "no data folder at $data_dir"
    fi
else
    info "the downloaded laws stay in ${SCRAPPER_DATA_DIR:-./legislatie-descarcata} (use --purge-data to delete them)"
fi

# --- done -------------------------------------------------------------------

step "Done"
if [ "$dry_run" = "1" ]; then
    info "dry run: nothing was changed."
fi
if [ -n "$uv_bin" ]; then
    uv_note="still installed in $HOME/.local/bin (it may serve other tools)"
else
    uv_note="not found on this machine"
fi
cat <<EOF

Uninstalled. What was left in place:

    uv                 $uv_note
    data folder        ${SCRAPPER_DATA_DIR:-./legislatie-descarcata}
    PATH entries       remove $tool_bin_dir from your shell profile by hand if you want

To drop uv as well:  uv self uninstall
EOF
