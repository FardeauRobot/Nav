# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A terminal file explorer (`src/nav.py`, 773 lines) plus its shell integration
(`src/nav.zsh`, 308 lines). Stdlib-only Python 3 + zsh: no dependencies, no build step, no
package manager, **no test suite**. `README.md` documents the user-facing surface (keys,
install, follow modes, colour keys) — this file covers what a reader of any single source
file would break by accident.

`install.sh` (POSIX sh, repo root) is the whole install: it appends one absolute
`source` line to `${ZDOTDIR:-$HOME}/.zshrc` and nothing else. It **copies
nothing** — `nav.zsh` self-locates via `NAV_HOME=${${(%):-%x}:A:h}`, so there is
no path to substitute and no second copy to drift. It is therefore the third
place that has to agree on where `nav.zsh` lives (with the README and the rc
file); its idempotence check is `grep -F` on that absolute path, so moving or
renaming the clone makes a re-run add a *second* line rather than recognise the
first. `--uninstall` removes the block, deliberately leaving `$NAV_STATE` alone.

```sh
./install.sh                # or: sh install.sh, --uninstall, --dry-run
source src/nav.zsh          # must be sourced, never executed
nav [dir]                   # `navigate` and `n` alias the same function
navigate leader|lead|follower|follow|solo|status   # follow on|off|toggle|status still works
navigate lead 3 | follow 3 | follow on 3 | status 3   # named groups; bare == group `default`

zsh -n src/nav.zsh                                            # parse-only, no terminal needed
python3 -c "import ast; ast.parse(open('src/nav.py').read())"
sh -n install.sh
```

Test `install.sh` against a fake `HOME` — and **`env -u ZDOTDIR` is not
optional** there: it resolves `${ZDOTDIR:-$HOME}`, so with `ZDOTDIR` exported in
the parent shell a bare `HOME=/tmp/x ./install.sh` appends to your *real* rc
file.

`python3 src/nav.py` run directly prints `navigateur: needs a terminal` and exits 2 unless
**both** stdin and stdout are TTYs — so the usual "pipe it and read the output" smoke test
cannot work here. Exercising a change means an interactive terminal.

Shared state lives under `$NAV_STATE` (default `~/.navigateur`; overridable, see
`src/nav.zsh:12` and `src/nav.py:35`): `config.toml`, `roles/<tty>`, and per group
`groups/<group>/{cwd,gen,sub/<tty>.fifo}`. Deleting that directory returns the project to
first-run behaviour. The `groups/` level is new: a follower live across that upgrade must
re-run `navigate follow` once, since the flat `cwd`/`sub/` it was reading are now dead.

## The two-process seam

Understand this before editing either file. A process can never `cd` its parent shell, so
`nav.py` does not try: it writes the chosen directory to the file named by `$NAV_LASTDIR`,
and `nav()` (`src/nav.zsh:292`) cds there after Python exits. That is why `nav.zsh` must be
sourced rather than run.

The direct consequence: **`nav.py`'s stdout is the display, never a data channel.** A stray
`print` corrupts the frame rather than leaking data. Anything the shell needs goes through
`$NAV_LASTDIR` or `$NAV_STATE`.

## Roles: the second seam

A window is `leader`, `follower` or solo **of one group**, and `$NAV_STATE/roles/<tty>` is
the **single source of truth** — not a variable in either process. It holds
`"<role> <group>"`; a bare `leader` with no second field is the pre-groups format and still
reads as group `default`, which is the only reason old role files survive an upgrade. That
file is the seam between the two processes: setting a role is all `nav.py` can do about one,
because only a shell can register a FIFO with `zle -F`. `_nav_reconcile()` (`src/nav.zsh`) brings the live machinery in line with
the file, and is called from `_nav_cmd`, from `nav()` after the browser exits, and from the
prompt hook — never anywhere else.

**The group is content-encoded here and path-encoded under `groups/`, and the split is
forced by which paths are hot.** `_nav_role_of` / `read_role()` read one file at a name they
already know and run at every prompt through `_nav_reconcile`; `roles/<group>/<tty>` would
make them glob to answer "which group is this tty in". `_nav_publish` / `publish()` have the
opposite pressure — they glob their subscribers on every leader prompt and every cursor
move, so membership must be a filesystem fact (`groups/<g>/sub/*.fifo`) rather than a
lookup. Deciding either one the other way puts a `roles/` glob on a hot path. There is
deliberately **no group registry file**: the directories under `groups/` plus the role files
are the registry, and a third list could disagree with both.

Consequences that are easy to undo by accident:

- **Never test a role file with `== leader*`.** With the group in the content that prefix
  matches *every* group's leader, so the exclusivity sweep would evict group 1 when someone
  takes group 3, and `_nav_find_leader` would accept a foreign group's leader as satisfying
  the follow guard. Both sites parse the line and compare role **and** group. `read_role()`
  returning a tuple is what makes the Python side fail to compile rather than fail silently.
- **`_nav_precmd`'s first line must stay a variable test.** `[[ -n $_NAV_ROLE ]] || return 0`
  is what keeps every non-participating shell on this machine from paying a `stat` per
  prompt. A window that *has* a role re-reads its own file (one file, never a glob) because
  another window pressing `F` can revoke it.
- **The leader publishes from zsh, and must never block.** Opening a reader-less FIFO for
  write blocks forever, which would wedge the line editor at every prompt. `_nav_publish`
  uses `sysopen -w -o nonblock` from `zsh/system`, probed once into `_NAV_HAVE_SYSOPEN`;
  without the module it writes the group's `cwd` only and followers degrade to lazy. Do not
  "simplify" it to `print > $fifo`.
- **`_NAV_FIFO` is recorded at follow-start, never recomputed at stop.** On a group switch
  `_nav_reconcile` has already moved `_NAV_GROUP` on by the time `_nav_follow_stop` runs, so
  a stop that rebuilt the path would unlink the *new* group's FIFO and leak the old one.
  `_NAV_FOLLOWING` holds the **group**, not a flag, for the same reason: it is what tells
  reconcile a switch happened at all.
- **`_NAV_GROUP` is empty for solo**, mirroring `_NAV_ROLE`, so every consumer defaults it —
  otherwise `n follow toggle` in a solo window builds a path with an empty component.
  `_NAV_LASTGROUP` is a per-shell convenience cache so toggling out of a group and back in
  returns you to it; it is **never** a source of truth, and the role file always decides.
  Python gets this for free: `set_role()` carries `self.group` through a `solo`.
- **A group name is a path component**, so `_nav_group_ok` / `group_ok()` must reject
  anything outside `[A-Za-z0-9_-]{1,32}` before a path is built from it — `n lead ../../etc`
  is the case that matters. The two implementations must accept **exactly** the same set,
  the same class of rule as the `_nav_tty` / `self_tty()` byte-identity one.
- **A role this shell never took is a leftover.** Tty names get recycled, so
  `_nav_drop_stale_role` clears one before the user interacts — and deliberately *not*
  after the browser runs, where it would delete the role `F`/`f` just wrote. Dropping it
  is only safe because **a window writes no role file but its own**: `write_role()` and
  `_nav_role_write` always target `self.me` / `$_NAV_TTY`, and the exclusivity sweep only
  ever *unlinks* another window's file, never creates one. Grant either of them the power
  to assign a role to a different tty and this guard starts eating real roles.
- **Exclusivity is enforced only on promotion**, in `write_role()` / `_nav_role_write`, and
  it is **one leader per group** — a sweep that is not group-scoped silently kills every
  other group. Nothing on a prompt path globs `roles/`. `_nav_find_leader` / `find_leader()`
  glob it too, which is why they are reachable only from the verbs and the `f` key — never from
  `_nav_reconcile`, which `_nav_precmd` runs at every prompt. Both **exclude the calling
  tty**: promoting to follower is refused when nothing else leads, and a leader that counted
  itself would pass that guard and demote, leaving nobody leading. The refusal lives at the
  top of `_nav_set_role`, not in `_nav_cmd`'s `follow` branch, because three paths promote
  (`follow`, `follow on`, `follow toggle`) and the setter is the one chokepoint — which is
  also why the group validation sits there rather than at each verb.
- **`_nav_cmd`'s `$2` is overloaded.** `follow`'s second word is a sub-verb
  (`on|off|toggle|status`) if it matches one and a *group name* otherwise, which is what
  makes `n follow 3` work; `n follow on 3` spells both out. A bare `lead` and a bare `follow`
  must resolve the group the *same* way — both take `$here`, the group this window is in (or
  the last one it was in, or `default`). Give `lead` a hardcoded `default` and `n lead` in a
  window already leading group 3 silently moves it and evicts `default`'s leader. `nav()`'s
  dispatcher gate still
  matches on `$1` only and passes `"$@"` through — miss that and `n follow 3` falls into the
  browser, where `3` is resolved as a *path*.
- **A ghost leader still counts.** A window killed without `zshexit` leaves a `leader` role
  file, and the guard accepts it — macOS `/dev/ttysNNN` persist, so there is no cheap
  liveness test. `_nav_drop_stale_role` clears it when that tty is next used.
- **Snap-back**: `nav()` unsets `_NAV_LED` after the browser exits, so a leader re-publishes
  `$PWD` on its next prompt. That single line is what brings followers home from a browse
  preview when you quit with `q` — and README documents it, so it is behaviour, not
  bookkeeping.

## One publish rule, one function

`Navigateur.published_dir()` (`src/nav.py:475`) is the nearest enclosing directory of the
highlighted row — the row itself when it is a folder, its parent when it is a file. Both the
following terminals *and* `↵`/`$NAV_LASTDIR` read this single function. A new feature that
needs "where am I" must call it, not recompute the rule. `maybe_publish()` de-dupes against
the last published value, which is what keeps arrowing between sibling files from firing,
and returns early unless the role is `leader` — a follower is structurally incapable of
publishing, rather than merely not doing so.

## Cross-file invariants

Each of these looks like removable noise and is load-bearing:

- **TTY name symmetry.** `_nav_tty()` (`src/nav.zsh`) and `self_tty()` (`src/nav.py`)
  must produce byte-identical strings — that one string is the FIFO stem, the self-exclusion
  key in `publish()`, *and* the `roles/<tty>` filename. Diverge and the driving terminal
  silently fights its own `cd`, or takes a role nobody can see. The rule is **the path under
  `/dev` with `/` mapped to `-`**, not a basename: a basename collapses Linux `/dev/pts/3` to
  `3`, colliding with the console `/dev/tty3`. macOS `/dev/ttys003` has no slash left to map,
  so the rule is a no-op there and pre-existing state stays valid.
- **A FIFO payload is one newline-terminated write** (`src/nav.py:303`). A partial line
  blocks `read -r` inside the `zle -F` callback and freezes that terminal's line editor.
- **`ENXIO` on open means "no reader"** — that terminal died without cleanup, so `publish()`
  unlinks the FIFO. The garbage collection deliberately free-rides on a write we were doing
  anyway, which also means it is **not comprehensive**: it only ever sweeps the group being
  published to, so a leftover in a group nobody leads any more waits until somebody does.
  That is no worse than the pre-groups "nobody ever publishes again" case; leave it alone.
- **Two follow paths, one state.** Live (`zle -F` on the per-tty FIFO, the only way to wake
  an idle zsh from outside) degrades to lazy (`_nav_precmd`, reading
  `$NAV_STATE/groups/<group>/cwd`).
  `_NAV_SEEN` is recorded *before* acting — including for a message deliberately not acted
  on — so the lazy path never drags the user back after they cd'd somewhere themselves.
  `Navigateur.sync_from_leader()` is the third reader of that state and obeys the same
  record-before-acting rule with `self.seen`. All three read the *group's* `cwd`. It **polls
  it rather than opening the FIFO**: the shell holds that FIFO open for `zle -F`, and a second reader would race it
  for the bytes, so the message would vanish before the shell could act on it. Its
  "we are already there" test is `published_dir()` **and** expanded-or-root, never
  `published_dir()` alone: a folder under the cursor is collapsed until something expands
  it, so the looser test left the leader's directory merely *selected* — and stuck, since
  every later message naming it took the same exit, `f` included.
- **`add-zsh-hook`, never `precmd_functions=(...)`.** Warp already has entries in that array
  and clobbering it breaks the terminal.
- **Rendering is Warp-shaped.** `Screen.paint()` diffs against the previous frame and
  repaints only changed lines; full clears flicker. `SIGWINCH` and a size change just blank
  `prev` to force a repaint. `measure()` clamps to 40x8 because a terminal reporting 0x0
  mid-resize would otherwise render an empty screen.
- **Config degrades key by key.** `load_config()` type-checks each key over `DEFAULTS`; a
  typo must never cost you the browser you would use to fix it. Adding a setting means
  touching `DEFAULTS`, `DEFAULT_CONFIG_TEXT` and the README table together — and note
  `DEFAULT_CONFIG_TEXT` is written only on first run, so for an existing user it is the merge
  against `DEFAULTS`, not the template, that actually supplies the new value.
- **Colours are plain 24-bit hex, not palette slots.** That is the reason this is raw ANSI
  rather than curses. Keep it that way.

## Scope

`start.txt` is the original user request. It explains *why* the bindings are what they are,
but it is **not** the spec of record — the README key table is authoritative where the two
differ. `start.txt` asks for "`h` to do `..`", while `leave()` (`src/nav.py:573`) is
collapse → jump-to-parent → re-root; `O`/reveal is not in the request at all. Do not "fix"
the implementation back toward `start.txt`.

README's "Not in this first pass" lists deliberate omissions (fuzzy search, file operations,
bookmarks, git decorations, a bash integration, following across machines).

**zsh is a hard requirement, not a preference** — bash has no `zle -F`, so a bash port would
silently lose live follow rather than fail loudly. On Fedora that means `dnf install zsh`.

`open_it()` and `spawn_terminal()` are the only platform-split code. Both dispatch on `IS_MAC`,
both gate the Linux branch on `has_display()` (`$DISPLAY`/`$WAYLAND_DISPLAY` — deliberately
*not* `$SSH_CONNECTION`, since X-forwarding gives an ssh session a real display), and both keep
the rule that **every unsupported case sets `self.message` and returns**: these keys must never
be the ones that raise out of the browser. The Linux spawn uses `launch_detached()`
(`Popen`, `start_new_session=True`) rather than `subprocess.run()`, because kitty, alacritty and
foot do not fork and `run()` would freeze the browser until the new window closed; macOS keeps
`run()`, since `open` and `osascript` hand off and return.

Following is **per machine** — `$NAV_STATE` is a filesystem seam, so a Mac terminal cannot lead
one on a remote box. Two ssh sessions into the same host do follow each other.
