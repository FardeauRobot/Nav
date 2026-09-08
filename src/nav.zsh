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
# writing without blocking when nobody is reading it.  A member publishes at
# every prompt, so a blocking open on a dead member's FIFO would wedge the
# line editor for good.  Probed once, here, rather than per publish.  Without
# the module we still write the group's cwd, so members degrade to lazy
# delivery rather than breaking.
zmodload zsh/system 2>/dev/null && _NAV_HAVE_SYSOPEN=1 || _NAV_HAVE_SYSOPEN=

# The path under /dev with `/` mapped to `-`: /dev/ttys003 -> ttys003 (macOS,
# unchanged), /dev/pts/3 -> pts-3 (Linux).  nav.py's self_tty() must produce the
# identical string: that one string is the FIFO stem, the self-exclusion key in
# publish(), *and* the roles/<tty> file name.  Not ${t:t} -- a Linux basename collapses
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

# --------------------------------------------------------------- group membership

# $NAV_STATE/roles/<tty> holds this window's group name -- "3", "work" -- and
# absent means solo.  A legacy "<role> <group>" line ("leader 3", "follower
# default") written before groups went leaderless is still read: the group is
# taken from it and the role word dropped, so old files stay valid.  That file
# is the seam between the two processes: nav.py sets membership by writing it and
# can do nothing else -- only a shell can register a FIFO with `zle -F` -- so the
# shell reconciles its live machinery to the file afterwards.
#
# The group lives in the *content* here and in the *path* under groups/ below,
# and that split is forced by which paths are hot.  _nav_group_read reads one
# file at a name it already knows, and runs at every prompt through
# _nav_reconcile; roles/<group>/<tty> would make it glob to answer "which group
# is this tty in".  Broadcasting has the opposite pressure -- see _nav_publish.

# A group name becomes a path component, so `n 3 ../../etc` has to be refused
# before any mkdir.  Glob-only and setopt-free on purpose: `##` would need
# EXTENDED_GLOB and =~ leans on regex support this file never otherwise assumes.
# nav.py's group_ok() must accept exactly this set -- the same class of rule as
# the _nav_tty / self_tty() byte-identity one.
_nav_group_ok() {
  [[ -n $1 && ${#1} -le 32 && $1 == ${1//[^A-Za-z0-9_-]/} ]]
}

# One group's broadcast directory: cwd, gen and sub/<tty>.fifo live under it.
_nav_gdir() { print -r -- $NAV_STATE/groups/$1 }

_nav_group_read() {              # $1 tty -> REPLY = group | "" (solo)
  local f=$NAV_STATE/roles/$1
  REPLY=
  [[ -r $f ]] || return 0
  local line; line=$(<$f)
  local -a parts; parts=(${=line})
  local g
  # Back-compat: drop a leading "leader"/"follower" word and take the group
  # from what follows (or `default` if nothing does).
  if [[ ${parts[1]:-} == (leader|follower) ]]; then
    g=${parts[2]:-default}
  else
    g=${parts[1]:-}
  fi
  _nav_group_ok $g && REPLY=$g    # junk must never cost you the browser
  return 0
}

_nav_group_write() {            # $1 tty  $2 group | "" to leave
  mkdir -p -- $NAV_STATE/roles 2>/dev/null || return 1
  if [[ -z $2 ]]; then
    rm -f -- $NAV_STATE/roles/$1
  else
    print -r -- "$2" >| $NAV_STATE/roles/$1 2>/dev/null || return 1
  fi
  return 0
}

# A role file for our tty that *this* shell never asked for is a leftover from
# a window that was killed before zshexit could clean up -- and tty names get
# reused freely.  An empty _NAV_GROUP is the proof that it was not us.  Called
# only where the user starts interacting, never from the prompt hook and never
# after the browser, so a group nav.py has just written always survives.
_nav_drop_stale_role() {
  [[ -n $_NAV_GROUP ]] && return 0
  [[ -e $NAV_STATE/roles/$_NAV_TTY ]] && rm -f -- $NAV_STATE/roles/$_NAV_TTY
  return 0
}

# Bring this terminal's live machinery in line with its role file, and cache the
# group in _NAV_GROUP (empty for solo -- that emptiness is the prompt fast path,
# and every consumer defaults an empty group to `default` so a solo window never
# builds a path with an empty component).
_nav_reconcile() {
  _nav_mytty || return 1
  local REPLY
  _nav_group_read $_NAV_TTY
  # A group change voids the seen-stamp and the "where I am synced" guard from
  # the old group.
  [[ $REPLY == $_NAV_GROUP ]] || unset _NAV_AT _NAV_SEEN
  _NAV_GROUP=$REPLY
  # A convenience cache, never a source of truth: leaving a group clears
  # _NAV_GROUP, and this is what lets the browser's `f` rejoin the last one.
  # The role file still decides what this window *is*.
  [[ -n $_NAV_GROUP ]] && _NAV_LASTGROUP=$_NAV_GROUP
  if [[ -n $_NAV_GROUP ]]; then
    # _NAV_FOLLOWING holds the *group*, not a flag: a window that switches groups
    # must re-register on the new FIFO instead of staying wired to the old one.
    [[ $_NAV_FOLLOWING == $_NAV_GROUP ]] || {
      [[ -n $_NAV_FOLLOWING ]] && _nav_follow_stop
      _nav_follow_start
    }
  else
    [[ -n $_NAV_FOLLOWING ]] && _nav_follow_stop
    unset _NAV_AT
  fi
  return 0
}

# ---------------------------------------------------------------- broadcasting

# The mirror of publish() in nav.py: one newline-terminated write per subscribed
# FIFO, self excluded, unlinking any FIFO nobody is reading.
_nav_publish() {                 # $1 tty (self, excluded)  $2 dir  $3 group
  local me=$1 dest=$2 f fd
  local gd=$NAV_STATE/groups/$3
  # sub/ too: a member can publish into a group nobody else has joined yet.
  mkdir -p -- $gd/sub 2>/dev/null || return 1
  print -r -- $dest >| $gd/cwd 2>/dev/null
  # A publish is an event, not a value.  Without this stamp a member
  # re-selecting the directory another has since left is byte-identical to
  # the stale file that member already declined, and _NAV_SEEN swallows it.
  print -r -- ${RANDOM}${RANDOM} >| $gd/gen 2>/dev/null
  [[ -n $_NAV_HAVE_SYSOPEN ]] || return 0    # lazy delivery only; still correct
  # The group is in the path, never looked up per subscriber.  This glob runs at
  # every member's prompt and every cursor move on the Python side; deciding
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
  if [[ -d $dest ]]; then                    # replayed by _nav_precmd later
    _NAV_AT=$dest       # echo suppressor: _nav_precmd sees PWD == $_NAV_AT next
                        # prompt and does not rebroadcast this followed cd
    if [[ $dest != $PWD ]]; then
      builtin cd -- "$dest"
      zle reset-prompt
    fi
  fi
}

# Silent: the reconciler calls these at a prompt, where a message would be
# spam.  Announcing is the job of the verbs in _nav_cmd.  Run for every member
# now, not just followers -- in a peer group everybody subscribes.
_nav_follow_start() {
  local g=${_NAV_GROUP:-default}
  local gd=$NAV_STATE/groups/$g
  # sub/ may not exist yet: a member can join before anyone ever publishes.
  # Skip this and mkfifo fails, the window degrades silently to lazy, and
  # nothing says why.
  mkdir -p -- $gd/sub 2>/dev/null || return 1
  # Recorded now, never recomputed at stop.  On a group switch _nav_reconcile has
  # already moved _NAV_GROUP on by the time _nav_follow_stop runs, so a stop that
  # rebuilt this path would unlink the *new* group's FIFO and leak the old one.
  _NAV_FIFO=$gd/sub/$_NAV_TTY.fifo
  _NAV_FOLLOWING=$g
  _NAV_SEEN=            # nothing seen yet: a window that joins mid-browse is
                        # owed the stored cwd at its next prompt.  _NAV_AT is
                        # left unset so _nav_precmd's fresh-join branch adopts
                        # the group's directory rather than seeding it with ours
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
  # Fast path.  A terminal in no group pays one variable test per prompt and
  # nothing else -- no stat, no fork.  Membership is only ever granted through
  # _nav_cmd (which reconciles) or by nav.py (reconciled by nav() on exit), so
  # a solo terminal can never miss one.
  [[ -n $_NAV_GROUP ]] || return 0
  # Membership can be dropped from the browser (`f`), so a window that has it
  # re-reads its own file -- one file, never a glob.
  _nav_reconcile
  [[ -n $_NAV_GROUP ]] || return 0
  local gd=$NAV_STATE/groups/$_NAV_GROUP
  if [[ -z $_NAV_AT ]]; then
    # Fresh join.  Adopt the group's current directory if it has one, else seed
    # the group with ours.
    if [[ -r $gd/cwd ]]; then
      local dest; dest=$(<$gd/cwd)
      _NAV_SEEN=$(_nav_msg $dest $_NAV_GROUP)
      _NAV_AT=$dest
      [[ -d $dest && $dest != $PWD ]] && builtin cd -- "$dest"
    else
      _NAV_AT=$PWD
      _nav_publish $_NAV_TTY $PWD $_NAV_GROUP
      _NAV_SEEN=$(_nav_msg $PWD $_NAV_GROUP)
    fi
  elif [[ $PWD != $_NAV_AT ]]; then
    # We moved ourselves since the last sync.  In a peer group our own cd is the
    # intentional event and wins: publish it and let the others converge.  A
    # plain sync-first order here would silently discard a cd that raced an
    # incoming broadcast.  Record $PWD as seen so the live reader and the lazy
    # branch below do not bounce us straight back.
    _NAV_AT=$PWD
    _nav_publish $_NAV_TTY $PWD $_NAV_GROUP
    _NAV_SEEN=$(_nav_msg $PWD $_NAV_GROUP)
  else
    # We have not moved: catch up to the group.  Lazy fallback, and the
    # self-correction for a live window that missed a FIFO wake.  _NAV_SEEN
    # keeps a message we already acted on from replaying.
    [[ -r $gd/cwd ]] || return 0
    local dest; dest=$(<$gd/cwd)
    local msg; msg=$(_nav_msg $dest $_NAV_GROUP)
    [[ $msg == $_NAV_SEEN ]] && return 0
    _NAV_SEEN=$msg
    _NAV_AT=$dest
    [[ -d $dest && $dest != $PWD ]] && builtin cd -- "$dest"
  fi
  return 0
}

_nav_cleanup() {
  [[ -n $_NAV_TTY ]] || return 0
  [[ -n $_NAV_FIFO ]] && rm -f -- $_NAV_FIFO
  [[ -n $_NAV_GROUP ]] && rm -f -- $NAV_STATE/roles/$_NAV_TTY
  return 0
}

# --------------------------------------------------------------- the verbs

_nav_announce() {
  if [[ -n $_NAV_GROUP ]]; then
    local m; [[ -n $_NAV_FD ]] && m=live || m="lazy (moves on your next prompt)"
    print -r -- "navigateur: in group $_NAV_GROUP ($_NAV_TTY) · $m"
  else
    print -r -- "navigateur: solo ($_NAV_TTY)"
  fi
}

_nav_join() {                   # $1 group name, or "solo" to leave
  if [[ $1 == solo ]]; then
    _nav_group_write $_NAV_TTY "" || {
      print -r -- "navigateur: cannot write $NAV_STATE/roles/$_NAV_TTY" >&2; return 1
    }
  else
    # The group becomes a directory component, so validate before any mkdir.
    _nav_group_ok $1 || {
      print -r -- "navigateur: bad group name '$1' — letters, digits, - and _ only, max 32" >&2
      return 2
    }
    _nav_group_write $_NAV_TTY $1 || {
      print -r -- "navigateur: cannot write $NAV_STATE/roles/$_NAV_TTY" >&2; return 1
    }
  fi
  unset _NAV_AT               # re-sync from scratch on the next prompt
  _nav_reconcile
  _nav_announce
}

_nav_status() {                  # $1 group, or empty for every group
  local g f members live mark last REPLY
  local -a groups
  if [[ -n $1 ]]; then
    _nav_group_ok $1 || {
      print -r -- "navigateur: bad group name '$1'" >&2; return 2
    }
    groups=($1)
  else
    for f in $NAV_STATE/groups/*(N/); do groups+=(${f:t}); done
    # A group can have members before it has a directory -- nobody has moved
    # yet -- so the role files are the other half of the registry.  There is no
    # groups.toml on purpose: a third list could disagree with both.
    for f in $NAV_STATE/roles/*(N); do
      _nav_group_read ${f:t}
      [[ -n $REPLY ]] || continue
      (( ${groups[(I)$REPLY]} )) || groups+=($REPLY)
    done
  fi
  _nav_announce
  if (( ! ${#groups} )); then
    print -r -- "  no groups"
    return 0
  fi
  for g in ${(o)groups}; do
    members=0
    for f in $NAV_STATE/roles/*(N); do
      _nav_group_read ${f:t}
      [[ $REPLY == $g ]] && (( members++ ))
    done
    live=0
    for f in $NAV_STATE/groups/$g/sub/*.fifo(N); do (( live++ )); done
    mark=; [[ -n $_NAV_GROUP && $g == $_NAV_GROUP ]] && mark=' ← this window'
    last=none
    [[ -r $NAV_STATE/groups/$g/cwd ]] && last=$(<$NAV_STATE/groups/$g/cwd)
    # A lazy member has no FIFO, so "live" trailing "member(s)" is normal, not a
    # fault.
    print -r -- "  $g: $members member(s), $live live · last broadcast: $last$mark"
  done
}

_nav_cmd() {
  _nav_mytty || { print -r -- "navigateur: no tty, cannot join a group" >&2; return 1 }
  _nav_drop_stale_role
  _nav_reconcile
  local target
  case $1 in
    solo)   _nav_join solo ;;
    status) _nav_status $2 ;;
    group)
      # `navigate group <name>` -- $2 is the group name, required.
      if [[ -z $2 ]]; then
        target=${_NAV_LASTGROUP:-default}
      elif _nav_group_ok $2; then
        target=$2
      else
        print -r -- "navigateur: bad group name '$2' — letters, digits, - and _ only, max 32" >&2
        return 2
      fi
      # Toggle: running it again for the group this window is already in leaves.
      if [[ -n $_NAV_GROUP && $target == $_NAV_GROUP ]]; then
        _nav_join solo
      else
        _nav_join $target
      fi ;;
    *)
      # `navigate <id>` -- $1 is itself the group (the `nav 3` shortcut), same
      # toggle rule.
      _nav_group_ok $1 || {
        print -r -- "navigateur: bad group name '$1' — letters, digits, - and _ only, max 32" >&2
        return 2
      }
      if [[ -n $_NAV_GROUP && $1 == $_NAV_GROUP ]]; then
        _nav_join solo
      else
        _nav_join $1
      fi ;;
  esac
}

# --------------------------------------------------------------- the browser

nav() {
  # `navigate group <name>`, `navigate solo` and `navigate status` manage this
  # window's peer-group membership instead of launching the browser.  Missed
  # here, `nav group` falls through to the browser, where nav.py resolves
  # "group" as a *path*, fails is_dir(), takes .parent, and opens $PWD instead.
  if [[ $1 == (group|solo|status) ]]; then
    local verb=$1; shift
    _nav_cmd $verb "$@"
    return
  fi
  # `nav 3` -- a lone run of digits -- is the shortcut for joining group 3.  `<->`
  # is core zsh numeric globbing, no setopt.  A directory literally named `3` is
  # still reachable as `nav ./3`.  `_nav_cmd`'s `*)` branch is the one that treats
  # $1 as a bare group id, so route straight to it rather than through `group`.
  if [[ $# -eq 1 && $1 == <-> ]]; then
    _nav_cmd "$1"
    return
  fi

  # Before the browser, because nav.py reads the role file too: it must not
  # open showing a group on the strength of a dead window's leftovers.
  _nav_mytty && _nav_drop_stale_role
  local before=$_NAV_GROUP

  local tmp; tmp=$(mktemp) || return 1
  NAV_LASTDIR=$tmp NAV_STATE=$NAV_STATE command python3 "$NAV_HOME/nav.py" "$@"
  local status_=$?
  if [[ -s $tmp ]]; then
    local dest; dest=$(<$tmp)
    [[ -d $dest ]] && builtin cd -- "$dest"
  fi
  rm -f -- "$tmp"
  # The browser may have changed this window's group (`f`), so reconcile now
  # rather than at the next prompt.
  unset _NAV_AT
  _nav_reconcile
  # Whatever the browser did, this window is now the group's reference point:
  # broadcast $PWD so the rest of the group converges here -- the directory picked
  # with `↵`, or the real cwd after `q` backed out of a browse.  nav.py's live
  # previews left stale dirs in groups/<g>/cwd while browsing (that is the point --
  # the others tracked the cursor), so without this re-publish _nav_precmd's
  # fresh-join branch would adopt a preview nobody chose.  This is the snap-back
  # the old leader model got by re-publishing $PWD once the browser exited.
  # Publish before _nav_msg: _nav_publish writes the gen file that _nav_msg reads.
  if [[ -n $_NAV_GROUP ]]; then
    _NAV_AT=$PWD
    _nav_publish $_NAV_TTY $PWD $_NAV_GROUP
    _NAV_SEEN=$(_nav_msg $PWD $_NAV_GROUP)
  elif [[ -n $before ]]; then
    # Left the group inside the browser (`f`).  Its other members still have this
    # window's browse previews in groups/$before/cwd; publish the real $PWD once
    # as a parting broadcast so they land somewhere real rather than on a dir this
    # window only previewed.  Nothing local to record -- this window is solo now.
    _nav_publish $_NAV_TTY $PWD $before
  fi
  return $status_
}

# add-zsh-hook, never `precmd_functions=(...)`: Warp already has entries in
# that array and clobbering it breaks the terminal.
autoload -Uz add-zsh-hook
add-zsh-hook precmd  _nav_precmd
add-zsh-hook zshexit _nav_cleanup

# Three names for one function: `navigate` reads best, `nav` is the short form
# the docs use, `n` is for muscle memory. Aliases, not copies of the function --
# `navigate group 3` still lands in the same code path.
alias navigate='nav'
alias n='nav'
