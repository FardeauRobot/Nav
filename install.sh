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

# For every `source .../nav.zsh` (or `. .../nav.zsh`) line in $RC, print one
# `<status> <lineno>` row: `ours` if the line resolves (same `cd`+`pwd -P`
# rule $REPO used above) to $NAV_ZSH, `other` if it resolves to a real but
# different file, `dangling` if the target no longer exists at all -- the
# tell for a clone that was later moved or renamed, as opposed to a genuinely
# separate install.  A literal grep on $NAV_ZSH would only catch a line
# spelled exactly the way this script writes it (quoted, absolute); a line
# sourcing the same clone written differently -- unquoted, tilde-form --
# wouldn't match, so a re-run would append a harmless-looking but real
# duplicate, and --uninstall would report nothing to remove.  Shared by the
# install-time check, --dry-run, --uninstall and the "another copy" notice,
# so all four agree on what a given line actually is.
classify_nav_lines() {
  [ -f "$RC" ] || return 0
  matches=$(grep -Fn 'nav.zsh' "$RC" 2>/dev/null) || return 0
  [ -n "$matches" ] || return 0
  tmp_matches=$(mktemp "${TMPDIR:-/tmp}/navigateur.XXXXXX")
  printf '%s\n' "$matches" > "$tmp_matches"
  while IFS=: read -r lineno rest; do
    # Two seds, not one `\(source\|\.\)` alternation: BSD sed (macOS's
    # /bin/sh) has no `\|`, and silently leaves an unmatched line untouched
    # rather than erroring, which would make this whole check a no-op.
    candidate=$(printf '%s\n' "$rest" | sed \
      -e 's/^[[:space:]]*source[[:space:]][[:space:]]*//' \
      -e 's/^\.[[:space:]][[:space:]]*//' \
      -e 's/^"\(.*\)"$/\1/' -e "s/^'\\(.*\\)'\$/\\1/")
    case $candidate in
      '~') candidate="$HOME" ;;
      '~/'*) candidate="$HOME/${candidate#'~/'}" ;;
    esac
    if [ ! -e "$candidate" ]; then
      printf 'dangling %s\n' "$lineno"
      continue
    fi
    resolved=$(CDPATH= cd -- "$(dirname -- "$candidate")" 2>/dev/null &&
      printf '%s/%s\n' "$(pwd -P)" "$(basename -- "$candidate")") || continue
    if [ "$resolved" = "$NAV_ZSH" ]; then
      printf 'ours %s\n' "$lineno"
    else
      printf 'other %s\n' "$lineno"
    fi
  done < "$tmp_matches"
  rm -f "$tmp_matches"
}

# The first line that's actually this clone, or nothing.
find_nav_line() {
  classify_nav_lines | awk '$1 == "ours" { print $2; exit }'
}

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
  target=$(find_nav_line)
  if [ -z "$target" ]; then
    note "nothing to do: $RC does not source $NAV_ZSH"
    exit 0
  fi
  # Never edit in place -- a truncated rc file is a shell that won't start.
  tmp=$(mktemp "${TMPDIR:-/tmp}/navigateur.XXXXXX")
  trap 'rm -f "$tmp"' EXIT
  # Drops the resolved line, the marker directly above it, *and* the blank
  # line install.sh put in front of them -- otherwise an install/uninstall
  # cycle leaves one blank behind every time.
  awk -v marker="$MARKER" -v target="$target" '
    { line[NR] = $0 }
    END {
      drop[target] = 1
      if (line[target-1] == marker) drop[target-1] = 1
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

# Computed before --dry-run branches on it, so a dry run reports the same
# thing a real run would do -- rather than always claiming it would append,
# even when the real run below would recognise an existing line and no-op.
target=$(find_nav_line)

if [ "$MODE" = dryrun ]; then
  if [ -n "$target" ]; then
    note "already installed -- $RC already sources $NAV_ZSH"
  else
    note "would append to $RC:"
    note ""
    note "$MARKER"
    note "$LINE"
  fi
  exit 0
fi

# ------------------------------------------------------------------- install

[ -f "$RC" ] || { : > "$RC"; note "created $RC"; }

if [ -n "$target" ]; then
  note "already installed -- $RC already sources $NAV_ZSH"
  note "run  exec zsh  if you haven't since, then type  navigate"
  exit 0
fi

# A different copy of navigateur already sourced, or a stale line left behind
# by a clone that was later moved/renamed?  Say so and carry on either way --
# an installer that stops to ask is worse than either outcome, and a second
# real source is harmless (the second source just redefines nav(), and the
# hooks go in via add-zsh-hook, which de-dupes by function name).  The two
# cases get different wording: calling a dead line "another" copy would be
# actively wrong for the moved-clone case classify_nav_lines exists to catch.
classification=$(classify_nav_lines)
if printf '%s\n' "$classification" | grep -q '^other '; then
  note "note: $RC already sources another nav.zsh:"
  grep -Fn 'nav.zsh' "$RC" | sed 's/^/      /'
  note "      adding this one too; the last one sourced wins."
elif printf '%s\n' "$classification" | grep -q '^dangling '; then
  note "note: $RC has a nav.zsh line pointing at a path that no longer exists"
  note "      (probably this clone, moved or renamed) -- adding the current one:"
  grep -Fn 'nav.zsh' "$RC" | sed 's/^/      /'
fi

printf '\n%s\n%s\n' "$MARKER" "$LINE" >> "$RC"

note "installed -- added to $RC:"
note "    $LINE"
note ""
note "Run  exec zsh  (or open a new terminal), then type  navigate"
note "Undo with  $REPO/install.sh --uninstall"
