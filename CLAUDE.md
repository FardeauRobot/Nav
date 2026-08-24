# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A terminal file explorer (`src/nav.py`, 576 lines) plus its shell integration
(`src/nav.zsh`, 135 lines). Stdlib-only Python 3 + zsh: no dependencies, no build step, no
package manager, **no test suite**. `README.md` documents the user-facing surface (keys,
install, follow modes, colour keys) — this file covers what a reader of any single source
file would break by accident.

```sh
source src/nav.zsh          # must be sourced, never executed
nav [dir]                   # `navigate` and `n` alias the same function
navigate follow on|off|toggle|status

zsh -n src/nav.zsh                                            # parse-only, no terminal needed
python3 -c "import ast; ast.parse(open('src/nav.py').read())"
```

`python3 src/nav.py` run directly prints `navigateur: needs a terminal` and exits 2 unless
**both** stdin and stdout are TTYs — so the usual "pipe it and read the output" smoke test
cannot work here. Exercising a change means an interactive terminal.

Shared state lives under `$NAV_STATE` (default `~/.navigateur`; overridable, see
`src/nav.zsh:8` and `src/nav.py:34`): `config.toml`, `cwd`, `roles/<tty>`, `sub/<tty>.fifo`.
Deleting that directory returns the project to first-run behaviour.

## The two-process seam

Understand this before editing either file. A process can never `cd` its parent shell, so
`nav.py` does not try: it writes the chosen directory to the file named by `$NAV_LASTDIR`,
and `nav()` (`src/nav.zsh:23`) cds there after Python exits. That is why `nav.zsh` must be
sourced rather than run.

The direct consequence: **`nav.py`'s stdout is the display, never a data channel.** A stray
`print` corrupts the frame rather than leaking data. Anything the shell needs goes through
`$NAV_LASTDIR` or `$NAV_STATE`.

## Roles: the second seam

A window is `leader`, `follower` or solo, and `$NAV_STATE/roles/<tty>` is the **single
source of truth** — not a variable in either process. That file is the seam between them:
setting a role is all `nav.py` can do about one, because only a shell can register a FIFO
with `zle -F`. `_nav_reconcile()` (`src/nav.zsh`) brings the live machinery in line with
the file, and is called from `_nav_cmd`, from `nav()` after the browser exits, and from the
prompt hook — never anywhere else.

Consequences that are easy to undo by accident:

- **`_nav_precmd`'s first line must stay a variable test.** `[[ -n $_NAV_ROLE ]] || return 0`
  is what keeps every non-participating shell on this machine from paying a `stat` per
  prompt. A window that *has* a role re-reads its own file (one file, never a glob) because
  another window pressing `F` can revoke it.
- **The leader publishes from zsh, and must never block.** Opening a reader-less FIFO for
  write blocks forever, which would wedge the line editor at every prompt. `_nav_publish`
  uses `sysopen -w -o nonblock` from `zsh/system`, probed once into `_NAV_HAVE_SYSOPEN`;
  without the module it writes `cwd` only and followers degrade to lazy. Do not "simplify"
  it to `print > $fifo`.
- **A role this shell never took is a leftover.** Tty names get recycled, so
  `_nav_drop_stale_role` clears one before the user interacts — and deliberately *not*
  after the browser runs, where it would delete the role `F`/`f` just wrote.
- **Exclusivity is enforced only on promotion**, in `write_role()` / `_nav_role_write`.
  Nothing on a prompt path globs `roles/`.
- **Snap-back**: `nav()` unsets `_NAV_LED` after the browser exits, so a leader re-publishes
  `$PWD` on its next prompt. That single line is what brings followers home from a browse
  preview when you quit with `q` — and README documents it, so it is behaviour, not
  bookkeeping.

## One publish rule, one function

`Navigateur.published_dir()` (`src/nav.py:336`) is the nearest enclosing directory of the
highlighted row — the row itself when it is a folder, its parent when it is a file. Both the
following terminals *and* `↵`/`$NAV_LASTDIR` read this single function. A new feature that
needs "where am I" must call it, not recompute the rule. `maybe_publish()` de-dupes against
the last published value, which is what keeps arrowing between sibling files from firing,
and returns early unless the role is `leader` — a follower is structurally incapable of
publishing, rather than merely not doing so.

## Cross-file invariants

Each of these looks like removable noise and is load-bearing:

- **TTY basename symmetry.** `_nav_tty()` (`src/nav.zsh`) and `self_tty()` (`src/nav.py`)
  must produce byte-identical strings — that one string is the FIFO stem, the self-exclusion
  key in `publish()`, *and* the `roles/<tty>` filename. Diverge and the driving terminal
  silently fights its own `cd`, or takes a role nobody can see.
- **A FIFO payload is one newline-terminated write** (`src/nav.py:190`). A partial line
  blocks `read -r` inside the `zle -F` callback and freezes that terminal's line editor.
- **`ENXIO` on open means "no reader"** — that terminal died without cleanup, so `publish()`
  unlinks the FIFO. The garbage collection deliberately free-rides on a write we were doing
  anyway.
- **Two follow paths, one state.** Live (`zle -F` on the per-tty FIFO, the only way to wake
  an idle zsh from outside) degrades to lazy (`_nav_precmd`, reading `$NAV_STATE/cwd`).
  `_NAV_SEEN` is recorded *before* acting — including for a message deliberately not acted
  on — so the lazy path never drags the user back after they cd'd somewhere themselves.
  `Navigateur.sync_from_leader()` is the third reader of that state and obeys the same
  record-before-acting rule with `self.seen`. It **polls `cwd` rather than opening the
  FIFO**: the shell holds that FIFO open for `zle -F`, and a second reader would race it
  for the bytes, so the message would vanish before the shell could act on it.
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
differ. `start.txt` asks for "`h` to do `..`", while `leave()` (`src/nav.py:376`) is
collapse → jump-to-parent → re-root; `O`/reveal is not in the request at all. Do not "fix"
the implementation back toward `start.txt`.

README's "Not in this first pass" lists deliberate omissions (fuzzy search, file operations,
bookmarks, git decorations, non-macOS `open` equivalents). `open` / `open -R` in `open_it()`
is macOS-only on purpose.
