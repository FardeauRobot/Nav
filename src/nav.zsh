# navigateur -- shell integration.  `install.sh` at the repo root puts the right
# absolute path to this file into your .zshrc; the line it writes is:
#
#     source "/wherever/you/cloned/navigateur/src/nav.zsh"
#
# No path in here needs editing: NAV_HOME below resolves it from the file zsh
# is sourcing (%x), so the repo can live anywhere and be moved afterwards.
#
# It MUST be sourced, not executed: nav() has to be a function so that its `cd`
# happens in *your* shell.  A script runs in a subshell and its cd dies with it.

: ${NAV_STATE:=$HOME/.navigateur}
NAV_HOME=${${(%):-%x}:A:h}

# zsh/system provides `sysopen -o nonblock`, the only way to open a FIFO for
# writing without blocking when nobody is reading it.  A leader publishes at
# every prompt, so a blocking open on a dead follower's FIFO would wedge the
# line editor for good.  Probed once, here, rather than per publish.  Without
# the module we still write the group's cwd, so followers degrade to lazy
# delivery rather than breaking.
zmodload zsh/system 2>/dev/null && _NAV_HAVE_SYSOPEN=1 || _NAV_HAVE_SYSOPEN=

# The path under /dev with `/` mapped to `-`: /dev/ttys003 -> ttys003 (macOS,
# unchanged), /dev/pts/3 -> pts-3 (Linux).  nav.py's self_tty() must produce the
# identical string: that one string is the FIFO stem, the self-exclusion key in
# publish(), *and* the role-file name.  Not ${t:t} -- a Linux basename collapses
# /dev/pts/3 to `3`, which collides with the console /dev/tty3.  $TTY is only set
# in interactive shells, hence the fallback.
_nav_tty() {
  local t=$TTY
  [[ -n $t ]] || t=$(tty 2>/dev/null)
  [[ $t == /dev/* ]] || return 1   # `tty` prints "not a tty" on stdout, so the
  t=${t#/dev/}                     # /dev/ prefix is the only safe check
  print -r -- ${t//\//-}
}

# Cached: $(...) forks, and the prompt hook must stay free for terminals that
# are not taking part.  Resolved on first need, never at source time.
_nav_mytty() {
  [[ -n $_NAV_TTY ]] && return 0
  _NAV_TTY=$(_nav_tty) || return 1
  [[ -n $_NAV_TTY ]]
}

# ------------------------------------------------------------------- the roles

# $NAV_STATE/roles/<tty> holds "<role> <group>" -- "leader 3", "follower default"
# -- and absent means solo.  A bare "leader" with no second field is the
# pre-groups format and still reads as group `default`, so old role files stay
# valid.  That file is the seam between the two processes: nav.py sets a role by
# writing it and can do nothing else -- only a shell can register a FIFO with
# `zle -F` -- so the shell reconciles its live machinery to the file afterwards.
#
# The group lives in the *content* here and in the *path* under groups/ below,
# and that split is forced by which paths are hot.  _nav_role_of reads one file
# at a name it already knows, and runs at every prompt through _nav_reconcile;
# roles/<group>/<tty> would make it glob to answer "which group is this tty in".
# Broadcasting has the opposite pressure -- see _nav_publish.

# A group name becomes a path component, so `n lead ../../etc` has to be refused
# before any mkdir.  Glob-only and setopt-free on purpose: `##` would need
# EXTENDED_GLOB and =~ leans on regex support this file never otherwise assumes.
# nav.py's group_ok() must accept exactly this set -- the same class of rule as
# the _nav_tty / self_tty() byte-identity one.
_nav_group_ok() {
  [[ -n $1 && ${#1} -le 32 && $1 == ${1//[^A-Za-z0-9_-]/} ]]
}

# One group's broadcast directory: cwd, gen and sub/<tty>.fifo live under it.
_nav_gdir() { print -r -- $NAV_STATE/groups/$1 }

_nav_role_of() {                 # $1 tty -> REPLY  = leader|follower|solo
  local f=$NAV_STATE/roles/$1    #           REPLY2 = group
  REPLY=solo REPLY2=default
  [[ -r $f ]] || return 0
  local line; line=$(<$f)
  # Split on whitespace -- NOT the strip this used to do, which would glue
  # "leader 3" into the single junk word "leader3".
  local -a parts; parts=(${=line})
  REPLY=${parts[1]:-}
  REPLY2=${parts[2]:-default}
  [[ $REPLY == (leader|follower) ]] && _nav_group_ok $REPLY2 || {
    REPLY=solo REPLY2=default    # a junk value must never cost you the browser
  }
  return 0
}

_nav_role_write() {              # $1 tty  $2 leader|follower|solo  $3 group
  mkdir -p -- $NAV_STATE/roles 2>/dev/null || return 1
  if [[ $2 == solo ]]; then
    rm -f -- $NAV_STATE/roles/$1
  else
    print -r -- "$2 $3" >| $NAV_STATE/roles/$1 2>/dev/null || return 1
  fi
  # Exactly one leader *per group*.  Enforced only here, on promotion -- nothing
  # on a prompt path ever globs this directory; each window reads its own file.
  if [[ $2 == leader ]]; then
    local f REPLY REPLY2
    for f in $NAV_STATE/roles/*(N); do
      [[ ${f:t} == $1 ]] && continue
      # Parsed, never `== leader*`.  With the group in the content that prefix
      # test matches every group's leader, so taking group 3 would silently
      # evict the window leading group 1.
      _nav_role_of ${f:t}
      [[ $REPLY == leader && $REPLY2 == $3 ]] && rm -f -- $f
    done
  fi
  return 0
}

# The tty leading $2, or empty.  This globs roles/, so it belongs to the verb
# paths only -- _nav_precmd reconciles at every prompt and must never stat the
# world.  $1 is a tty to skip, and skipping self is the whole point at the follow
# guard: a leader asking "is there someone to follow?" must not find *itself*,
# pass, and demote -- that would leave nobody leading, which is exactly the state
# the refusal exists to prevent.  Empty $1 skips nothing, which is what `status`
# wants.
_nav_find_leader() {             # $1 tty to exclude (may be empty)  $2 group
  local f want=$2 REPLY2
  REPLY=
  for f in $NAV_STATE/roles/*(N); do
    [[ -n $1 && ${f:t} == $1 ]] && continue
    _nav_role_of ${f:t}
    [[ $REPLY == leader && $REPLY2 == $want ]] && { REPLY=${f:t}; return 0 }
  done
  REPLY=                         # _nav_role_of left its own answer in there
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
# role in _NAV_ROLE (empty for solo -- that emptiness is the prompt fast path)
# and the group in _NAV_GROUP (empty for solo too, so every consumer defaults it
# to `default`; without that `n follow toggle` in a solo window would build a
# path with an empty component).
_nav_reconcile() {
  _nav_mytty || return 1
  local REPLY REPLY2
  _nav_role_of $_NAV_TTY
  [[ $REPLY == solo ]] && { REPLY= ; REPLY2= }
  # A group change is as disruptive as a role change: a leader owes its new
  # group a publish, and a follower must not carry a seen-stamp across groups.
  [[ $REPLY2 == $_NAV_GROUP ]] || unset _NAV_LED _NAV_SEEN
  _NAV_ROLE=$REPLY
  _NAV_GROUP=$REPLY2
  # A convenience cache, never a source of truth: going solo clears _NAV_GROUP,
  # and without this `n follow toggle` twice in a group-3 window would come back
  # in `default`. The role file still decides what this window *is*.
  [[ -n $_NAV_GROUP ]] && _NAV_LASTGROUP=$_NAV_GROUP
  if [[ $_NAV_ROLE == follower ]]; then
    # _NAV_FOLLOWING holds the *group*, not a flag: a window that switches groups
    # must re-register on the new FIFO instead of staying wired to the old one.
    [[ $_NAV_FOLLOWING == $_NAV_GROUP ]] || {
      [[ -n $_NAV_FOLLOWING ]] && _nav_follow_stop
      _nav_follow_start
    }
  else
    [[ -n $_NAV_FOLLOWING ]] && _nav_follow_stop
  fi
  [[ $_NAV_ROLE == leader ]] || unset _NAV_LED
  return 0
}

# ---------------------------------------------------------------- broadcasting

# The mirror of publish() in nav.py: one newline-terminated write per subscribed
# FIFO, self excluded, unlinking any FIFO nobody is reading.
_nav_publish() {                 # $1 tty (self, excluded)  $2 dir  $3 group
  local me=$1 dest=$2 f fd
  local gd=$NAV_STATE/groups/$3
  # sub/ too: a leader can publish into a group no follower has joined yet.
  mkdir -p -- $gd/sub 2>/dev/null || return 1
  print -r -- $dest >| $gd/cwd 2>/dev/null
  # A publish is an event, not a value.  Without this stamp a leader
  # re-selecting the directory a follower has since left is byte-identical to
  # the stale file that follower already declined, and _NAV_SEEN swallows it.
  print -r -- ${RANDOM}${RANDOM} >| $gd/gen 2>/dev/null
  [[ -n $_NAV_HAVE_SYSOPEN ]] || return 0    # lazy delivery only; still correct
  # The group is in the path, never looked up per subscriber.  This glob runs at
  # every leader prompt and every cursor move on the Python side; deciding
  # membership by reading roles/ here would put a second glob plus N file reads
  # on the hottest path in the program.
  for f in $gd/sub/*.fifo(N); do
    [[ ${${f:t}:r} == $me ]] && continue     # never fight our own shell's cd
    if sysopen -w -o nonblock -u fd -- $f 2>/dev/null; then
      print -u $fd -r -- $dest 2>/dev/null   # a partial line would block the
      exec {fd}>&-                           # reader inside its zle -F callback
      unset fd
    else
      rm -f -- $f    # ENXIO: no reader.  That terminal died without cleanup, so
    fi               # the sweep free-rides on a write we were doing anyway.
  done               # It only ever covers the group being published to.
  return 0
}

# ----------------------------------------------------------------- following

# The identity of the last broadcast in one group: its generation stamp and its
# directory.  _NAV_SEEN holds one of these, never a bare path.
_nav_msg() {                     # $1 dir  $2 group
  local gen= f=$NAV_STATE/groups/$2/gen
  [[ -r $f ]] && gen=$(<$f)
  print -r -- "$gen:$1"
}

# Live path: zle -F wakes the line editor while the shell sits idle at a
# prompt.  An idle zsh is blocked reading its TTY and cannot otherwise be made
# to run anything from outside -- this is the only way in.
_nav_reader() {
  local dest
  IFS= read -r -u $1 dest || { zle -F $1; return }
  _NAV_SEEN=$(_nav_msg $dest $_NAV_GROUP)   # record before acting: a message we
                                # consumed but chose not to act on must not be
  if [[ -d $dest && $dest != $PWD ]]; then   # replayed by _nav_precmd later
    builtin cd -- "$dest"
    zle reset-prompt
  fi
}

# Silent: the reconciler calls these at a prompt, where a message would be
# spam.  Announcing is the job of the verbs in _nav_cmd.
_nav_follow_start() {
  local g=${_NAV_GROUP:-default}
  local gd=$NAV_STATE/groups/$g
  # sub/ may not exist yet: a follower can join before the leader ever
  # publishes.  Skip this and mkfifo fails, the window degrades silently to
  # lazy, and nothing says why.
  mkdir -p -- $gd/sub 2>/dev/null || return 1
  # Recorded now, never recomputed at stop.  On a group switch _nav_reconcile has
  # already moved _NAV_GROUP on by the time _nav_follow_stop runs, so a stop that
  # rebuilt this path would unlink the *new* group's FIFO and leak the old one.
  _NAV_FIFO=$gd/sub/$_NAV_TTY.fifo
  _NAV_FOLLOWING=$g
  _NAV_SEEN=            # nothing seen yet: a window that starts following
                        # mid-browse is owed the stored cwd at its next prompt
  # Try the live path; fall back to the precmd path rather than failing.  Both
  # read the same state, so degrading costs only immediacy.
  if [[ -o interactive ]]; then
    [[ -p $_NAV_FIFO ]] || mkfifo -m 600 -- $_NAV_FIFO 2>/dev/null
    if [[ -p $_NAV_FIFO ]] && exec {_NAV_FD}<>$_NAV_FIFO 2>/dev/null; then
      zle -F $_NAV_FD _nav_reader 2>/dev/null || {
        exec {_NAV_FD}>&-; unset _NAV_FD; rm -f -- $_NAV_FIFO
      }
    fi
  fi
  return 0
}

_nav_follow_stop() {
  [[ -n $_NAV_FD ]] && { zle -F $_NAV_FD 2>/dev/null; exec {_NAV_FD}>&-; unset _NAV_FD }
  [[ -n $_NAV_FIFO ]] && rm -f -- $_NAV_FIFO
  unset _NAV_FOLLOWING _NAV_SEEN _NAV_FIFO
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
      _nav_publish $_NAV_TTY $PWD $_NAV_GROUP
      ;;
    follower)
      # Lazy fallback, and the self-correction for a live terminal that missed
      # an update.  _NAV_SEEN keeps it from dragging you back after you
      # deliberately cd somewhere yourself.
      local gd=$NAV_STATE/groups/$_NAV_GROUP
      [[ -r $gd/cwd ]] || return 0
      local dest; dest=$(<$gd/cwd)
      local msg; msg=$(_nav_msg $dest $_NAV_GROUP)
      [[ $msg == $_NAV_SEEN ]] && return 0
      _NAV_SEEN=$msg
      [[ -d $dest && $dest != $PWD ]] && builtin cd -- "$dest"
      ;;
  esac
  return 0
}

_nav_cleanup() {
  [[ -n $_NAV_TTY ]] || return 0
  [[ -n $_NAV_FIFO ]] && rm -f -- $_NAV_FIFO
  [[ -n $_NAV_ROLE ]] && rm -f -- $NAV_STATE/roles/$_NAV_TTY
  return 0
}

# --------------------------------------------------------------- the verbs

_nav_announce() {
  case $_NAV_ROLE in
    leader)
      print -r -- "navigateur: leading group $_NAV_GROUP ($_NAV_TTY) · its followers track this window" ;;
    follower)
      local m; [[ -n $_NAV_FD ]] && m=live || m="lazy (moves on your next prompt)"
      print -r -- "navigateur: following group $_NAV_GROUP ($_NAV_TTY) · $m" ;;
    *)
      print -r -- "navigateur: solo ($_NAV_TTY)" ;;
  esac
}

_nav_set_role() {                # $1 role  $2 group (default: `default`)
  local g=${2:-default}
  # Validated here rather than at each verb: the group becomes a directory
  # component, and this is the one chokepoint every promotion passes through.
  _nav_group_ok $g || {
    print -r -- "navigateur: bad group name '$g' — letters, digits, - and _ only, max 32" >&2
    return 2
  }
  # Refuse to follow nothing.  Guarded here and not in _nav_cmd's `follow`
  # branch because three paths promote to follower (`follow`, `follow on`,
  # `follow toggle`) and this is the one chokepoint all of them pass through --
  # a fourth added later is covered for free.  The role file is left untouched.
  if [[ $1 == follower ]]; then
    local REPLY; _nav_find_leader $_NAV_TTY $g
    if [[ -z $REPLY ]]; then
      print -r -- "navigateur: no leader for group $g — run \`n lead $g\` in the window that should lead" >&2
      return 2
    fi
  fi
  _nav_role_write $_NAV_TTY $1 $g || {
    print -r -- "navigateur: cannot write $NAV_STATE/roles/$_NAV_TTY" >&2; return 1
  }
  unset _NAV_LED               # a fresh leader publishes on its next prompt
  _nav_reconcile
  _nav_announce
}

_nav_status() {                  # $1 group, or empty for every group
  local g f n leader mark last REPLY REPLY2
  local -a groups
  if [[ -n $1 ]]; then
    _nav_group_ok $1 || {
      print -r -- "navigateur: bad group name '$1'" >&2; return 2
    }
    groups=($1)
  else
    for f in $NAV_STATE/groups/*(N/); do groups+=(${f:t}); done
    # A group can have a leader before it has a directory -- nothing has been
    # published yet -- so the role files are the other half of the registry.
    # There is no groups.toml on purpose: a third list could disagree with both.
    for f in $NAV_STATE/roles/*(N); do
      _nav_role_of ${f:t}
      [[ $REPLY == solo ]] && continue
      (( ${groups[(I)$REPLY2]} )) || groups+=($REPLY2)
    done
  fi
  _nav_announce
  if (( ! ${#groups} )); then
    print -r -- "  no groups"
    return 0
  fi
  for g in ${(o)groups}; do
    n=0
    for f in $NAV_STATE/groups/$g/sub/*.fifo(N); do (( n++ )); done
    _nav_find_leader '' $g       # no exclusion: status reports what is there
    leader=$REPLY
    mark=; [[ -n $_NAV_GROUP && $g == $_NAV_GROUP ]] && mark=' ← this window'
    if [[ -z $leader ]]; then
      print -r -- "  $g: leader none$mark"
    elif [[ $leader == $_NAV_TTY ]]; then
      print -r -- "  $g: leader this window$mark"
    else
      print -r -- "  $g: leader $leader$mark"
    fi
    last=none
    [[ -r $NAV_STATE/groups/$g/cwd ]] && last=$(<$NAV_STATE/groups/$g/cwd)
    # A lazy follower has no FIFO, so "0 live" while someone is following is
    # normal, not a fault.
    print -r -- "    $n terminal(s) live-subscribed · last broadcast: $last"
  done
}

_nav_cmd() {
  _nav_mytty || { print -r -- "navigateur: no tty, cannot take a role" >&2; return 1 }
  _nav_drop_stale_role
  _nav_reconcile
  # The group a bare verb means: the one this window is in, the last one it was
  # in if it has gone solo since, `default` if it has never joined one.  `lead`
  # and `follow` must agree on this -- `n lead` in a window already leading
  # group 3 has to be a no-op, not a silent move to `default` that evicts
  # whoever was leading there.
  local here=${_NAV_GROUP:-${_NAV_LASTGROUP:-default}}
  case $1 in
    lead|leader) _nav_set_role leader ${2:-$here} ;;
    solo)   _nav_set_role solo ;;
    status) _nav_status $2 ;;
    follow|follower)
      # $2 is overloaded.  A sub-verb keeps this window's group -- the last one
      # it used if it is currently solo, `default` if it never joined one --
      # while anything else is read as a group name, which is what makes
      # `n follow 3` work.  `n follow on 3` spells both out.
      case ${2:-on} in
        on)     _nav_set_role follower ${3:-$here} ;;
        off)    _nav_set_role solo ;;
        toggle) [[ $_NAV_ROLE == follower ]] && _nav_set_role solo \
                                             || _nav_set_role follower ${3:-$here} ;;
        status) _nav_status $3 ;;
        *)  _nav_group_ok $2 || {
              print -r -- "usage: navigate follow [on|off|toggle|status] [group]" >&2
              print -r -- "       navigate follow <group>" >&2
              return 2
            }
            _nav_set_role follower $2 ;;
      esac ;;
  esac
}

# --------------------------------------------------------------- the browser

nav() {
  # `leader`/`follower` are aliases, not renames: `lead`/`follow` are documented
  # and keep working.  This pattern has to learn the new names too -- miss it and
  # `n leader` falls through to the browser, where nav.py resolves "leader" as a
  # *path*, fails is_dir(), takes .parent, and silently opens $PWD instead.
  if [[ $1 == (lead|leader|follow|follower|solo|status) ]]; then
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
