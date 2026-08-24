# navigateur -- shell integration.  Source this from ~/.zshrc:
#
#     source ~/Desktop/navigateur/src/nav.zsh
#
# It MUST be sourced, not executed: nav() has to be a function so that its `cd`
# happens in *your* shell.  A script runs in a subshell and its cd dies with it.

: ${NAV_STATE:=$HOME/.navigateur}
NAV_HOME=${${(%):-%x}:A:h}

# /dev/ttys003 -> ttys003.  nav.py's os.ttyname() basename must produce the
# identical string or self-exclusion silently fails and the driving terminal
# fights its own cd.  $TTY is only set in interactive shells, hence the fallback.
_nav_tty() {
  local t=$TTY
  [[ -n $t ]] || t=$(tty 2>/dev/null)
  [[ $t == /dev/* ]] || return 1   # `tty` prints "not a tty" on stdout, so the
  print -r -- ${t:t}               # /dev/ prefix is the only safe check
}

# --------------------------------------------------------------- the browser

nav() {
  if [[ $1 == follow ]]; then
    shift
    _nav_follow "$@"
    return
  fi

  local tmp; tmp=$(mktemp) || return 1
  NAV_LASTDIR=$tmp NAV_STATE=$NAV_STATE command python3 "$NAV_HOME/nav.py" "$@"
  local status_=$?
  if [[ -s $tmp ]]; then
    local dest; dest=$(<$tmp)
    [[ -d $dest ]] && builtin cd -- "$dest"
  fi
  rm -f -- "$tmp"
  return $status_
}

# ---------------------------------------------------------------- following

# Live path: zle -F wakes the line editor while the shell sits idle at a
# prompt.  An idle zsh is blocked reading its TTY and cannot otherwise be made
# to run anything from outside -- this is the only way in.
_nav_reader() {
  local dest
  IFS= read -r -u $1 dest || { zle -F $1; return }
  _NAV_SEEN=$dest          # record before acting: a message we consumed but
  if [[ -d $dest && $dest != $PWD ]]; then   # chose not to act on must not be
    builtin cd -- "$dest"                    # replayed by _nav_precmd later
    zle reset-prompt
  fi
}

# Lazy fallback: catches up on your next prompt.  Also self-corrects a terminal
# that missed a live update.  _NAV_SEEN keeps it from dragging you back after
# you deliberately cd somewhere yourself.
_nav_precmd() {
  [[ -n $_NAV_FOLLOWING ]] || return 0
  [[ -r $NAV_STATE/cwd ]] || return 0
  local dest; dest=$(<$NAV_STATE/cwd)
  [[ $dest == $_NAV_SEEN ]] && return 0
  _NAV_SEEN=$dest
  [[ -d $dest && $dest != $PWD ]] && builtin cd -- "$dest"
  return 0
}

_nav_follow() {
  local t; t=$(_nav_tty)
  local fifo=$NAV_STATE/sub/$t.fifo
  case ${1:-toggle} in
    on)
      [[ -n $t ]] || { print -r -- "navigateur: no tty, cannot follow" >&2; return 1 }
      [[ -n $_NAV_FOLLOWING ]] && { print -r -- "navigateur: already following ($t)"; return 0 }
      mkdir -p -- $NAV_STATE/sub || return 1
      _NAV_FOLLOWING=1
      _NAV_SEEN=$PWD
      # Try the live path; fall back to the precmd path rather than failing.
      # Both read the same state, so degrading costs only immediacy.
      local mode="lazy (moves on your next prompt)"
      if [[ -o interactive ]]; then
        [[ -p $fifo ]] || mkfifo -m 600 -- $fifo 2>/dev/null
        if [[ -p $fifo ]] && exec {_NAV_FD}<>$fifo 2>/dev/null; then
          if zle -F $_NAV_FD _nav_reader 2>/dev/null; then
            mode="live"
          else
            exec {_NAV_FD}>&-; unset _NAV_FD; rm -f -- $fifo
          fi
        fi
      fi
      print -r -- "navigateur: following ($t) · $mode"
      ;;
    off)
      [[ -n $_NAV_FD ]] && { zle -F $_NAV_FD 2>/dev/null; exec {_NAV_FD}>&-; unset _NAV_FD }
      [[ -n $t ]] && rm -f -- $fifo
      unset _NAV_FOLLOWING _NAV_SEEN
      print -r -- "navigateur: not following"
      ;;
    status)
      local n; n=$(ls -1 $NAV_STATE/sub 2>/dev/null | wc -l | tr -d ' ')
      if [[ -n $_NAV_FOLLOWING ]]; then
        local m; [[ -n $_NAV_FD ]] && m=live || m=lazy
        print -r -- "following ($t) · $m · $n terminal(s) subscribed"
      else
        print -r -- "not following · $n other terminal(s) subscribed"
      fi
      ;;
    toggle)
      [[ -n $_NAV_FOLLOWING ]] && _nav_follow off || _nav_follow on
      ;;
    *)
      print -r -- "usage: navigate follow [on|off|toggle|status]" >&2
      return 2
      ;;
  esac
}

_nav_cleanup() {
  local t; t=$(_nav_tty)
  [[ -n $_NAV_FOLLOWING && -n $t ]] && rm -f -- $NAV_STATE/sub/$t.fifo
  return 0
}

# add-zsh-hook, never `precmd_functions=(...)`: Warp already has entries in
# that array and clobbering it breaks the terminal.
autoload -Uz add-zsh-hook
add-zsh-hook precmd  _nav_precmd
add-zsh-hook zshexit _nav_cleanup

# Three names for one function: `navigate` reads best, `nav` is the short form
# the docs use, `n` is for muscle memory. Aliases, not copies of the function --
# `navigate follow on` still lands in the same code path.
alias navigate='nav'
alias n='nav'
