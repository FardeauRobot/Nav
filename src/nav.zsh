# navigateur -- shell integration.  Source this from ~/.zshrc:
#
#     source ~/Desktop/navigateur/src/nav.zsh
#
# It MUST be sourced, not executed: nav() has to be a function so that its `cd`
# happens in *your* shell.  A script runs in a subshell and its cd dies with it.

: ${NAV_STATE:=$HOME/.navigateur}
NAV_HOME=${${(%):-%x}:A:h}

# zsh/system provides `sysopen -o nonblock`, the only way to open a FIFO for
# writing without blocking when nobody is reading it.  A leader publishes at
# every prompt, so a blocking open on a dead follower's FIFO would wedge the
# line editor for good.  Probed once, here, rather than per publish.  Without
# the module we still write $NAV_STATE/cwd, so followers degrade to lazy
# delivery rather than breaking.
zmodload zsh/system 2>/dev/null && _NAV_HAVE_SYSOPEN=1 || _NAV_HAVE_SYSOPEN=

# /dev/ttys003 -> ttys003.  nav.py's os.ttyname() basename must produce the
# identical string: that one string is the FIFO stem, the self-exclusion key in
# publish(), *and* the role-file name.  $TTY is only set in interactive shells,
# hence the fallback.
_nav_tty() {
  local t=$TTY
  [[ -n $t ]] || t=$(tty 2>/dev/null)
  [[ $t == /dev/* ]] || return 1   # `tty` prints "not a tty" on stdout, so the
  print -r -- ${t:t}               # /dev/ prefix is the only safe check
}

# Cached: $(...) forks, and the prompt hook must stay free for terminals that
# are not taking part.  Resolved on first need, never at source time.
_nav_mytty() {
  [[ -n $_NAV_TTY ]] && return 0
  _NAV_TTY=$(_nav_tty) || return 1
  [[ -n $_NAV_TTY ]]
}

# ------------------------------------------------------------------- the roles

# $NAV_STATE/roles/<tty> holds "leader" or "follower"; absent means solo.  That
# file is the seam between the two processes: nav.py sets a role by writing it
# and can do nothing else -- only a shell can register a FIFO with `zle -F` --
# so the shell reconciles its live machinery to the file afterwards.

_nav_role_of() {                 # $1 tty -> REPLY = leader|follower|solo
  local f=$NAV_STATE/roles/$1
  REPLY=solo
  [[ -r $f ]] || return 0
  REPLY=$(<$f)
  REPLY=${REPLY//[[:space:]]/}
  [[ $REPLY == (leader|follower) ]] || REPLY=solo   # a junk value must never
  return 0                                          # cost you the browser
}

_nav_role_write() {              # $1 tty  $2 leader|follower|solo
  mkdir -p -- $NAV_STATE/roles 2>/dev/null || return 1
  if [[ $2 == solo ]]; then
    rm -f -- $NAV_STATE/roles/$1
  else
    print -r -- $2 >| $NAV_STATE/roles/$1 2>/dev/null || return 1
  fi
  # Exactly one leader.  Enforced only here, on promotion -- nothing on a
  # prompt path ever globs this directory; each window reads its own file.
  if [[ $2 == leader ]]; then
    local f
    for f in $NAV_STATE/roles/*(N); do
      [[ ${f:t} == $1 ]] && continue
      [[ $(<$f) == leader* ]] && rm -f -- $f
    done
  fi
  return 0
}

# A role file for our tty that *this* shell never asked for is a leftover from
# a window that was killed before zshexit could clean up -- and tty names get
# reused freely.  An empty _NAV_ROLE is the proof that it was not us.  Called
# only where the user starts interacting, never from the prompt hook and never
# after the browser, so a role nav.py has just written always survives.
_nav_drop_stale_role() {
  [[ -n $_NAV_ROLE ]] && return 0
  [[ -e $NAV_STATE/roles/$_NAV_TTY ]] && rm -f -- $NAV_STATE/roles/$_NAV_TTY
  return 0
}

# Bring this terminal's live machinery in line with its role file, and cache the
# role in _NAV_ROLE (empty for solo -- that emptiness is the prompt fast path).
_nav_reconcile() {
  _nav_mytty || return 1
  local REPLY
  _nav_role_of $_NAV_TTY
  [[ $REPLY == solo ]] && REPLY=
  _NAV_ROLE=$REPLY
  if [[ $_NAV_ROLE == follower ]]; then
    [[ -n $_NAV_FOLLOWING ]] || _nav_follow_start
  else
    [[ -n $_NAV_FOLLOWING ]] && _nav_follow_stop
  fi
  [[ $_NAV_ROLE == leader ]] || unset _NAV_LED
  return 0
}

# ---------------------------------------------------------------- broadcasting

# The mirror of publish() in nav.py: one newline-terminated write per subscribed
# FIFO, self excluded, unlinking any FIFO nobody is reading.
_nav_publish() {                 # $1 tty (self, excluded)  $2 dir
  local me=$1 dest=$2 f fd
  mkdir -p -- $NAV_STATE 2>/dev/null || return 1
  print -r -- $dest >| $NAV_STATE/cwd 2>/dev/null
  [[ -n $_NAV_HAVE_SYSOPEN ]] || return 0    # lazy delivery only; still correct
  for f in $NAV_STATE/sub/*.fifo(N); do
    [[ ${${f:t}:r} == $me ]] && continue     # never fight our own shell's cd
    if sysopen -w -o nonblock -u fd -- $f 2>/dev/null; then
      print -u $fd -r -- $dest 2>/dev/null   # a partial line would block the
      exec {fd}>&-                           # reader inside its zle -F callback
      unset fd
    else
      rm -f -- $f    # ENXIO: no reader.  That terminal died without cleanup, so
    fi               # the sweep free-rides on a write we were doing anyway.
  done
  return 0
}

# ----------------------------------------------------------------- following

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

# Silent: the reconciler calls these at a prompt, where a message would be
# spam.  Announcing is the job of the verbs in _nav_cmd.
_nav_follow_start() {
  local fifo=$NAV_STATE/sub/$_NAV_TTY.fifo
  mkdir -p -- $NAV_STATE/sub 2>/dev/null || return 1
  _NAV_FOLLOWING=1
  _NAV_SEEN=$PWD
  # Try the live path; fall back to the precmd path rather than failing.  Both
  # read the same state, so degrading costs only immediacy.
  if [[ -o interactive ]]; then
    [[ -p $fifo ]] || mkfifo -m 600 -- $fifo 2>/dev/null
    if [[ -p $fifo ]] && exec {_NAV_FD}<>$fifo 2>/dev/null; then
      zle -F $_NAV_FD _nav_reader 2>/dev/null || {
        exec {_NAV_FD}>&-; unset _NAV_FD; rm -f -- $fifo
      }
    fi
  fi
  return 0
}

_nav_follow_stop() {
  [[ -n $_NAV_FD ]] && { zle -F $_NAV_FD 2>/dev/null; exec {_NAV_FD}>&-; unset _NAV_FD }
  [[ -n $_NAV_TTY ]] && rm -f -- $NAV_STATE/sub/$_NAV_TTY.fifo
  unset _NAV_FOLLOWING _NAV_SEEN
  return 0
}

# ------------------------------------------------------------------ the prompt

_nav_precmd() {
  # Fast path.  A terminal with no role pays one variable test per prompt and
  # nothing else -- no stat, no fork.  Roles are only ever granted through
  # _nav_cmd (which reconciles) or by nav.py (reconciled by nav() on exit), so
  # a solo terminal can never miss one.
  [[ -n $_NAV_ROLE ]] || return 0
  # A role can be revoked from *another* window -- pressing F there demotes the
  # leader here -- so a terminal that already has one re-reads its own file.
  # One file, never a glob.
  _nav_reconcile
  case $_NAV_ROLE in
    leader)
      [[ $PWD == $_NAV_LED ]] && return 0
      _NAV_LED=$PWD
      _nav_publish $_NAV_TTY $PWD
      ;;
    follower)
      # Lazy fallback, and the self-correction for a live terminal that missed
      # an update.  _NAV_SEEN keeps it from dragging you back after you
      # deliberately cd somewhere yourself.
      [[ -r $NAV_STATE/cwd ]] || return 0
      local dest; dest=$(<$NAV_STATE/cwd)
      [[ $dest == $_NAV_SEEN ]] && return 0
      _NAV_SEEN=$dest
      [[ -d $dest && $dest != $PWD ]] && builtin cd -- "$dest"
      ;;
  esac
  return 0
}

_nav_cleanup() {
  [[ -n $_NAV_TTY ]] || return 0
  [[ -n $_NAV_FOLLOWING ]] && rm -f -- $NAV_STATE/sub/$_NAV_TTY.fifo
  [[ -n $_NAV_ROLE ]] && rm -f -- $NAV_STATE/roles/$_NAV_TTY
  return 0
}

# --------------------------------------------------------------- the verbs

_nav_announce() {
  case $_NAV_ROLE in
    leader)
      print -r -- "navigateur: leading ($_NAV_TTY) · followers track this window" ;;
    follower)
      local m; [[ -n $_NAV_FD ]] && m=live || m="lazy (moves on your next prompt)"
      print -r -- "navigateur: following ($_NAV_TTY) · $m" ;;
    *)
      print -r -- "navigateur: solo ($_NAV_TTY)" ;;
  esac
}

_nav_set_role() {
  _nav_role_write $_NAV_TTY $1 || {
    print -r -- "navigateur: cannot write $NAV_STATE/roles/$_NAV_TTY" >&2; return 1
  }
  unset _NAV_LED               # a fresh leader publishes on its next prompt
  _nav_reconcile
  _nav_announce
}

_nav_status() {
  local f n=0 leader=
  for f in $NAV_STATE/sub/*.fifo(N); do (( n++ )); done
  for f in $NAV_STATE/roles/*(N); do
    [[ $(<$f) == leader* ]] && leader=${f:t}
  done
  _nav_announce
  if [[ -z $leader ]]; then
    print -r -- "  leader: none"
  elif [[ $leader == $_NAV_TTY ]]; then
    print -r -- "  leader: this window"
  else
    print -r -- "  leader: $leader"
  fi
  local last=none
  [[ -r $NAV_STATE/cwd ]] && last=$(<$NAV_STATE/cwd)
  # A lazy follower has no FIFO, so "0 live" while someone is following is
  # normal, not a fault.
  print -r -- "  $n terminal(s) live-subscribed · last broadcast: $last"
}

_nav_cmd() {
  _nav_mytty || { print -r -- "navigateur: no tty, cannot take a role" >&2; return 1 }
  _nav_drop_stale_role
  _nav_reconcile
  case $1 in
    lead)   _nav_set_role leader ;;
    solo)   _nav_set_role solo ;;
    status) _nav_status ;;
    follow)
      case ${2:-on} in
        on)     _nav_set_role follower ;;
        off)    _nav_set_role solo ;;
        toggle) [[ $_NAV_ROLE == follower ]] && _nav_set_role solo || _nav_set_role follower ;;
        status) _nav_status ;;
        *) print -r -- "usage: navigate follow [on|off|toggle|status]" >&2; return 2 ;;
      esac ;;
  esac
}

# --------------------------------------------------------------- the browser

nav() {
  if [[ $1 == (lead|follow|solo|status) ]]; then
    local verb=$1; shift
    _nav_cmd $verb "$@"
    return
  fi

  # Before the browser, because nav.py reads the role file too: it must not
  # open showing " leading " on the strength of a dead window's leftovers.
  _nav_mytty && _nav_drop_stale_role

  local tmp; tmp=$(mktemp) || return 1
  NAV_LASTDIR=$tmp NAV_STATE=$NAV_STATE command python3 "$NAV_HOME/nav.py" "$@"
  local status_=$?
  if [[ -s $tmp ]]; then
    local dest; dest=$(<$tmp)
    [[ -d $dest ]] && builtin cd -- "$dest"
  fi
  rm -f -- "$tmp"
  # The browser may have changed *this* window's role (F / f), so reconcile now
  # rather than at the next prompt.  Clearing _NAV_LED forces a leader to
  # re-publish $PWD: that is the snap-back that brings followers home from a
  # browse preview when you quit with `q`.
  unset _NAV_LED
  _nav_reconcile
  return $status_
}

# add-zsh-hook, never `precmd_functions=(...)`: Warp already has entries in
# that array and clobbering it breaks the terminal.
autoload -Uz add-zsh-hook
add-zsh-hook precmd  _nav_precmd
add-zsh-hook zshexit _nav_cleanup

# Three names for one function: `navigate` reads best, `nav` is the short form
# the docs use, `n` is for muscle memory. Aliases, not copies of the function --
# `navigate lead` still lands in the same code path.
alias navigate='nav'
alias n='nav'
