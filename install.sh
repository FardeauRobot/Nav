#!/bin/sh
# navigateur -- installer.
#
# Run it once after cloning:  ./install.sh   (or: sh install.sh)
#
# There is nothing to build and nothing to copy.  src/nav.zsh resolves its own
# location at source time (NAV_HOME=${${(%):-%x}:A:h}), so installing means
# exactly one thing: putting a correct absolute `source` line into your .zshrc.
# That is why this script only ever touches that one file.

set -eu

MARKER='# navigateur -- terminal file explorer'

# The repo is found from the script, never from $PWD, so `./install.sh` and
# `~/somewhere/navigateur/install.sh` behave the same.  CDPATH= keeps a user's
# CDPATH from sending `cd` somewhere else entirely.
REPO=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
NAV_ZSH="$REPO/src/nav.zsh"

die() { printf '%s\n' "install.sh: $*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }

[ -f "$NAV_ZSH" ] || die "can't find src/nav.zsh next to this script ($REPO).
Run install.sh from inside the cloned repo."

# ------------------------------------------------------------ where to write

# ZDOTDIR wins when it is set: those users have no ~/.zshrc at all, and writing
# one there would be writing into a file zsh never reads.
RC="${ZDOTDIR:-$HOME}/.zshrc"

usage() {
  cat <<EOF
usage: install.sh [--uninstall] [--dry-run]

  (no flags)    add the source line to $RC
  --uninstall   remove it again (leaves ~/.navigateur alone)
  --dry-run     print what would be written, change nothing
EOF
}

MODE=install
for arg in "$@"; do
  case $arg in
    --uninstall) MODE=uninstall ;;
    --dry-run)   MODE=dryrun ;;
    -h|--help)   usage; exit 0 ;;
    *)           usage >&2; die "unknown option: $arg" ;;
  esac
done

# ------------------------------------------------------------------ uninstall

if [ "$MODE" = uninstall ]; then
  [ -f "$RC" ] || { note "nothing to do: $RC does not exist"; exit 0; }
  if ! grep -Fq "$NAV_ZSH" "$RC"; then
    note "nothing to do: $RC does not source $NAV_ZSH"
    exit 0
  fi
  # Never edit in place -- a truncated rc file is a shell that won't start.
  tmp=$(mktemp "${TMPDIR:-/tmp}/navigateur.XXXXXX")
  trap 'rm -f "$tmp"' EXIT
  # Drops our two lines *and* the blank line install.sh put in front of them --
  # otherwise an install/uninstall cycle leaves one blank behind every time.
  awk -v navzsh="$NAV_ZSH" -v marker="$MARKER" '
    { line[NR] = $0; drop[NR] = (index($0, navzsh) || $0 == marker) }
    END {
      for (i = 1; i <= NR; i++)
        if (drop[i] && i > 1 && line[i-1] == "" && !drop[i-1]) { drop[i-1] = 1; break }
      for (i = 1; i <= NR; i++) if (!drop[i]) print line[i]
    }' "$RC" > "$tmp"
  cat "$tmp" > "$RC"          # `cat >` rather than `mv`: keeps the rc file's
  note "removed the navigateur line from $RC"   # own mode and any symlink
  note "your settings and state are still in ${NAV_STATE:-$HOME/.navigateur} -- delete it by hand if you want them gone."
  note "run  exec zsh  to drop navigate from this shell."
  exit 0
fi

# --------------------------------------------------------------- prerequisites

# zsh is a hard requirement, not a preference: live follow rides on `zle -F`,
# which bash has no equivalent of.  Failing loudly here beats installing
# something that would quietly lose half its point.
if ! command -v zsh >/dev/null 2>&1; then
  printf '%s\n' "install.sh: zsh is required and isn't installed." >&2
  printf '%s\n' "  Fedora/RHEL   sudo dnf install zsh" >&2
  printf '%s\n' "  Debian/Ubuntu sudo apt install zsh" >&2
  printf '%s\n' "  macOS         it ships with the system; check your PATH" >&2
  printf '%s\n' "Then re-run this script." >&2
  exit 1
fi

# src/nav.zsh calls `command python3` literally, so this name is the one that
# has to resolve -- a `python` alias or a venv shim doesn't help.
command -v python3 >/dev/null 2>&1 ||
  die "python3 is required and isn't on PATH."

# Python < 3.11 has no tomllib, and nav.py degrades to its built-in colours
# rather than failing.  So: a warning, not an abort.
if ! python3 -c 'import tomllib' >/dev/null 2>&1; then
  note "warning: this python3 has no tomllib (needs 3.11+)."
  note "         navigateur runs fine, but config.toml will be ignored and"
  note "         the built-in colours apply."
fi

LINE="source \"$NAV_ZSH\""

if [ "$MODE" = dryrun ]; then
  note "would append to $RC:"
  note ""
  note "$MARKER"
  note "$LINE"
  exit 0
fi

# ------------------------------------------------------------------- install

[ -f "$RC" ] || { : > "$RC"; note "created $RC"; }

# -F, not a plain grep: a repo path is not a regular expression, and a `+` or
# `[` in a directory name would otherwise make this check lie.
if grep -Fq "$NAV_ZSH" "$RC"; then
  note "already installed -- $RC already sources $NAV_ZSH"
  note "run  exec zsh  if you haven't since, then type  navigate"
  exit 0
fi

# A different copy of navigateur already sourced?  Say so and carry on: an
# installer that stops to ask is worse than either outcome, and the overlap is
# harmless -- the second source just redefines nav(), and the hooks go in via
# add-zsh-hook, which de-dupes by function name.
if grep -Fq 'nav.zsh' "$RC"; then
  note "note: $RC already sources another nav.zsh:"
  grep -Fn 'nav.zsh' "$RC" | sed 's/^/      /'
  note "      adding this one too; the last one sourced wins."
fi

printf '\n%s\n%s\n' "$MARKER" "$LINE" >> "$RC"

note "installed -- added to $RC:"
note "    $LINE"
note ""
note "Run  exec zsh  (or open a new terminal), then type  navigate"
note "Undo with  $REPO/install.sh --uninstall"
