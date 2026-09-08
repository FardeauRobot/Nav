# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A terminal file explorer (`src/nav.py`, ~3150 lines) plus its shell integration
(`src/nav.zsh`, ~470 lines). Stdlib-only Python 3 + zsh: no dependencies, no build step, no
package manager, **no test suite**. `README.md` documents the user-facing surface (keys,
install, peer groups, colour keys) — this file covers what a reader of any single source
file would break by accident.

`install.sh` (POSIX sh, repo root) is the whole install: it appends one absolute
`source` line to `${ZDOTDIR:-$HOME}/.zshrc` and nothing else. It **copies
nothing** — `nav.zsh` self-locates via `NAV_HOME=${${(%):-%x}:A:h}`, so there is
no path to substitute and no second copy to drift. It is therefore the third
place that has to agree on where `nav.zsh` lives (with the README and the rc
file); `find_nav_line()` is the one place that decides whether an rc line is
"ours": it resolves any existing `source .../nav.zsh` line to a real path
(`cd`+`pwd -P`, the same rule `$REPO` uses) and compares that, not the literal
text — so a line spelled differently from what `install.sh` itself writes
(tilde vs absolute, quoted vs not) is still recognised as the same clone.
Install, `--dry-run` and `--uninstall` all call it, which is what keeps them
agreeing — a `--dry-run` that predicted "would append" while a real run right
after it silently no-op'd would be its own bug. `find_nav_line()` sits on top
of `classify_nav_lines()`, which tags every `nav.zsh` line in the rc file
`ours` / `other` / `dangling` (target doesn't exist — the tell for a clone
moved or renamed since that line was written); the install-time "you already
source another nav.zsh" notice reads that classification too, so a dangling
line is reported honestly as a stale line from this same clone rather than
claimed to be a competing copy. It still can't *repair* the install once the
clone has moved — the old line's target is gone, so nothing resolves it to
`ours` — a re-run adds a fresh, correct second line alongside the dead one
rather than editing the first in place. `--uninstall` drops the resolved line
plus its marker and leading blank, deliberately leaving `$NAV_STATE` alone.

```sh
./install.sh                # or: sh install.sh, --uninstall, --dry-run
source src/nav.zsh          # must be sourced, never executed
nav [dir]                   # `navigate` and `n` alias the same function
navigate 3 | navigate group work | navigate solo | navigate status   # peer-group membership
                            # `navigate 3` toggles group 3; `navigate ./3` browses a dir named 3

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
cannot work here. Two harnesses do work, and they reach different layers:

- **Import it.** The module imports fine, and `frame()` / `panel_frame()` are pure
  `(cols, height) -> list[str]`, so layout arithmetic, the keymap and the config writer can
  all be asserted headlessly. Point `NAV_STATE` at a temp directory first — or the harness
  edits your real `~/.navigateur/config.toml` — load `src/nav.py` with `importlib`, and
  assert `len(frame(c, h)) == h` plus every ANSI-stripped line fitting in `c`, at both 40x8
  and something large. `ast.parse` alone catches none of that.
- **Give it a pty.** `pty.fork()` provides genuine TTYs on both fds, so the whole program
  runs and can be driven with `os.write`. This is the only way to reach `Screen.paint()`'s
  diff, the `SIGWINCH` path and `Screen.key()`'s escape decoding. Two traps: replay the
  output into a **persistent** grid, because `paint()` emits only the lines it thinks
  changed — one frame's bytes are a delta, not a picture, and a per-frame grid reports every
  unchanged row as blank, which quietly turns box-alignment checks into no-ops. And keep
  draining the pty while the child exits, or it blocks writing its restore sequences into a
  full buffer and looks like a program that refused to quit.

Neither replaces a look in a real terminal: `len()` is not display width, so a glyph that
renders double-width (an emoji-presentation `⌨`, say) passes both harnesses and tears the
box on screen.

Shared state lives under `$NAV_STATE` (default `~/.navigateur`; overridable, see
`src/nav.zsh:12` and `src/nav.py:44`): `config.toml`, `roles/<tty>` (holds this window's
group name, absent = solo — the filename kept its pre-groups spelling), and per group
`groups/<group>/{cwd,gen,sub/<tty>.fifo}`. Deleting that directory returns the project to
first-run behaviour. Two upgrades leave state that still reads: a `roles/<tty>` file
containing `leader 3` / `follower default` (the pre-peer format — the role word is dropped
and the group taken from what follows, `default` for a bare `leader`), and, older still,
the pre-`groups/` flat `cwd`/`sub/`. A window that only re-`source`s `nav.zsh` across
either upgrade may stay lazy until it re-runs `navigate <group>` once; a fresh shell picks
the group up on its first prompt.

## The two-process seam

Understand this before editing either file. A process can never `cd` its parent shell, so
`nav.py` does not try: it writes the chosen directory to the file named by `$NAV_LASTDIR`,
and `nav()` (`src/nav.zsh:411`) cds there after Python exits. That is why `nav.zsh` must be
sourced rather than run.

The direct consequence: **`nav.py`'s stdout is the display, never a data channel.** A stray
`print` corrupts the frame rather than leaking data. Anything the shell needs goes through
`$NAV_LASTDIR` or `$NAV_STATE`.

## Groups: the second seam

A window is **in one peer group** or **solo**, and `$NAV_STATE/roles/<tty>` is the **single
source of truth** — not a variable in either process. It holds the bare group name (`3`,
`work`); absent means solo. A legacy `"<role> <group>"` line (`leader 3`, `follower
default`, or a bare `leader`) is still parsed for back-compat: the role word is dropped and
the group taken from what follows (`default` when nothing does). There is **no leader** —
every member of a group both broadcasts its directory and follows the others'. That file is
the seam between the two processes: writing it is all `nav.py` can do about membership,
because only a shell can register a FIFO with `zle -F`. `_nav_reconcile()` (`src/nav.zsh`)
brings the live machinery in line with the file, and is called from `_nav_cmd`, from
`nav()` after the browser exits, and from the prompt hook — never anywhere else.

**The group is content-encoded here and path-encoded under `groups/`, and the split is
forced by which paths are hot.** `_nav_group_read` / `read_group()` read one file at a name
they already know and run at every prompt through `_nav_reconcile`; `roles/<group>/<tty>`
would make them glob to answer "which group is this tty in". `_nav_publish` / `publish()`
have the opposite pressure — they glob their subscribers on every member's prompt and every
cursor move, so membership must be a filesystem fact (`groups/<g>/sub/*.fifo`) rather than a
lookup. Deciding either one the other way puts a `roles/` glob on a hot path. There is
deliberately **no group registry file**: the directories under `groups/` plus the role
files are the registry, and a third list could disagree with both.

Consequences that are easy to undo by accident:

- **`_nav_precmd`'s first line must stay a variable test.** `[[ -n $_NAV_GROUP ]] || return 0`
  is what keeps every non-participating shell on this machine from paying a `stat` per
  prompt. A window that *is* in a group re-reads its own file (one file, never a glob)
  because pressing `f` in the browser can drop the membership.
- **Echo suppression is `_NAV_AT` / `self.published`, set on send *and* receive.** With
  every member both publishing and following, A's `cd /x` reaches B, B `cd`s to `/x`, and B
  must not rebroadcast `/x`. Three things carry this: `publish()` / `_nav_publish` skip the
  caller's own FIFO; the `gen` stamp (`_nav_msg` / `read_msg`, held in `_NAV_SEEN` /
  `self.seen`) distinguishes a fresh broadcast from a stale file already seen; and
  `_NAV_AT` ("the dir I am synced to for this group") is written on **both** the send path
  (`_nav_precmd`'s own-move branch, `nav()`'s post-browser re-publish) and the receive
  paths (`_nav_reader`, `_nav_precmd`'s lazy catch-up). The next `_nav_precmd` sees
  `PWD == $_NAV_AT` and does not re-publish. `sync_from_group()` does the analogous thing
  by setting `self.published = str(self.published_dir())` right after `reveal_path()` —
  `published_dir()`, not `dest`, because the two can differ by normalisation and
  `maybe_publish()` compares against `published_dir()`.
- **`_nav_precmd`'s three-way order lets a member's own `cd` win a race.** Keyed on
  `_NAV_AT`: **unset** → fresh join, adopt `groups/<g>/cwd` if it exists (record
  `_NAV_SEEN`, set `_NAV_AT`), else seed the group with `$PWD`; **`$PWD != $_NAV_AT`** →
  we moved ourselves, publish `$PWD` and record it as seen, *return* — our `cd` is the
  intentional event and beats a broadcast that landed in the same prompt gap; **else** →
  lazy catch-up (read `groups/<g>/cwd`, compare `_nav_msg` to `_NAV_SEEN`, act on a new
  one). A plain sync-then-publish would silently discard a `cd` that raced an incoming
  broadcast. `_nav_follow_start` leaves `_NAV_AT` unset on purpose so a fresh joiner takes
  the adopt branch rather than dragging the group to itself.
- **Snap-back is now an explicit re-publish in `nav()`.** After the browser exits, if the
  window is still in a group, `nav()` sets `_NAV_AT=$PWD` and `_nav_publish`es `$PWD`: this
  is what brings the group home from a browse preview on `q`, and lands everyone on the
  chosen dir on `↵`. nav.py's live previews deliberately overwrite `groups/<g>/cwd` while
  you browse, so without this re-publish `_nav_precmd`'s fresh-join branch would adopt a
  preview nobody chose. If `f` *left* a group inside the browser, `nav()` instead publishes
  `$PWD` once to the group it left, so that group's other members are not stranded on this
  window's last preview. README documents the `q` snap-back, so it is behaviour, not
  bookkeeping.
- **A member publishes from zsh, and must never block.** Opening a reader-less FIFO for
  write blocks forever, which would wedge the line editor at every prompt. `_nav_publish`
  uses `sysopen -w -o nonblock` from `zsh/system`, probed once into `_NAV_HAVE_SYSOPEN`;
  without the module it writes the group's `cwd`/`gen` only and members degrade to lazy. Do
  not "simplify" it to `print > $fifo`.
- **`_NAV_FIFO` is recorded at follow-start, never recomputed at stop.** On a group switch
  `_nav_reconcile` has already moved `_NAV_GROUP` on by the time `_nav_follow_stop` runs, so
  a stop that rebuilt the path would unlink the *new* group's FIFO and leak the old one.
  `_NAV_FOLLOWING` holds the **group**, not a flag, for the same reason: it is what tells
  reconcile a switch happened at all.
- **`_NAV_GROUP` is empty for solo**, so every consumer defaults it (`${_NAV_GROUP:-default}`)
  — otherwise a solo window builds a path with an empty component. `_NAV_LASTGROUP` (shell)
  and `self.last_group` (nav.py) are per-process toggle memory so `f` / `navigate <same>`
  can rejoin the group you just left; they are **never** a source of truth — the role file
  always decides what the window *is*. `self.last_group` is stashed in `join_group(None)`
  right before leaving.
- **A group name is a path component**, so `_nav_group_ok` / `group_ok()` must reject
  anything outside `[A-Za-z0-9_-]{1,32}` before a path is built from it — `navigate group
  ../../etc` is the case that matters. The two implementations must accept **exactly** the
  same set, the same class of rule as the `_nav_tty` / `self_tty()` byte-identity one.
- **A role file this shell never took is a leftover.** Tty names get recycled, so
  `_nav_drop_stale_role` clears one before the user interacts — and deliberately *not*
  after the browser runs, where it would delete the membership `f` just wrote. Dropping it
  is only safe because **a window writes no role file but its own**: `write_group()` and
  `_nav_group_write` always target `self.me` / `$_NAV_TTY`. Grant either of them the power
  to write another tty's file and this guard starts eating real memberships. There is no
  exclusivity sweep any more — a peer group has no slot to be exclusive about.
- **`nav()`'s dispatcher gate matches on `$1` only.** `navigate group|solo|status` route to
  `_nav_cmd`; a lone run of digits (`<->`, core zsh globbing, no `setopt`) routes to
  `_nav_cmd "$1"`, whose `*)` branch treats `$1` as a bare group id and toggles. Everything
  else launches the browser. Miss the digit gate and `navigate 3` falls into the browser,
  where `3` is resolved as a *path*.
- **A ghost membership still counts as live.** A window killed without `zshexit` leaves its
  `roles/<tty>` file and its `groups/<g>/sub/<tty>.fifo` — macOS `/dev/ttysNNN` persist, so
  there is no cheap liveness test. `_nav_drop_stale_role` clears the role file when that tty
  is next used; the stale FIFO is swept by the next `ENXIO`-on-open in that group (see
  Cross-file invariants).

## One publish rule, one function

`Navigateur.published_dir()` (`src/nav.py:1400`) is the nearest enclosing directory of the
highlighted row — the row itself when it is a folder, its parent when it is a file — **except**
in the one window between `reveal_path()` teleporting the tree and the next deliberate cursor
move, when `self.root` itself is the target and has no row to read it off; see `root_selected`
below. Both the following terminals *and* `↵`/`$NAV_LASTDIR` read this single function. A new
feature that needs "where am I" must call it, not recompute the rule. `maybe_publish()` de-dupes
against the last published value, which is what keeps arrowing between sibling files from
firing, and returns early when `self.group is None` — a solo window is structurally incapable
of publishing, rather than merely not doing so. Every window that *is* in a group publishes;
there is no leader gate any more (echo suppression is `self.published` / `_NAV_AT`, see
"Groups: the second seam").

`self.root` is structurally never a row — `rebuild()` only walks its *children* into `self.rows`
— so `reveal_path()`'s re-root branches (bookmark jump, group-sync jump) have no row to select
onto and used to fall back to `cursor = 0`. Since `list_dir()` sorts directories before files,
row 0 of a real folder is almost always a subdirectory of the target, not the target — so
`published_dir()` reported one level too deep until the user's next cursor move (the reported
bug: `↵` right after a bookmark jump cd'd into a subfolder of the bookmark). `root_selected` is
the fix: true only from the instant `reveal_path()` teleports until the next deliberate cursor
move (`move()`, `leave()`'s two navigational branches, `top`/`bottom`) — `enter()` deliberately
does **not** clear it, since expanding the highlighted row to peek inside it never moves the
cursor and must not silently change what `↵` will confirm.

## Opening a file with `↵`

`↵` is two-way: a **file** opens via `open_file()`, a **folder** does the old
`self.chosen = self.published_dir(); return`. (It used to be three-way — a pending
move/copy/cut/delete confirmed here too — but the verb keys `m`/`c`/`x`/`d` now run their
own confirm the moment they are pressed, so there is nothing left for `↵` to confirm; see
"Move/copy/cut/delete".) `open_file()` routes on `row.path.suffix.lower()` against
`MD_SUFFIXES` / `EDIT_SUFFIXES` / `EDIT_NAMES` (module level, next to `LINUX_TERMINALS`) and
falls through to `open_it()` for everything else — so the desktop handoff stays the single
default rather than being reimplemented.

- **The `root_selected` gate is the whole subtlety.** `published_dir()` deliberately ignores
  the row in the window after a `reveal_path()` teleport, because `self.root` is structurally
  never a row. A file-opening `↵` that read `self.current()` without that gate would hijack
  the first `↵` after every bookmark jump and open whatever file `list_dir()`'s sort put on
  row 0 — the exact class of bug `root_selected` was added to fix. The branch is
  `not self.root_selected and row is not None and not row.is_dir`.
- **Opening a file falls through, never `return`s** — the message has to reach the next
  paint, the same reason `try_op()`/`try_delete()` do after a confirm.
- **`editor_argv()` is the one editor resolver**, shared by `E` (`edit_it()`) and by `↵`
  (`_run_editor()`), for the same reason `key_ok()` and `group_ok()` are single: two copies
  would drift. `$VISUAL` → `$EDITOR` → `nvim` → `vim`, `shlex.split` so `code -w` survives.
  `edit_it()` used to hardcode `"nvim"`; the fallback chain makes that a no-op wherever
  neither variable is set.
- **`_run_editor()` takes `list[Path]` and has four call sites** — `edit_it()` (`E`),
  `open_file()` (`↵` on a source file), `follow_link()` (a link in the reader) and the
  reader's own `E`. Only `E` ever passes more than one path: `_edit_targets()` returns the
  marked set (sorted strings — never a walk of `self.rows`, which would drop a mark in a
  collapsed subtree) or the highlighted row. More than one path gets `-o` **only** when
  `Path(argv[0]).name` is `nvim`/`vim` — `-o` is a vim split flag and a filename to anything
  else. `select_path()` runs only for the single-path case; a multi-open leaves the cursor
  put. `E` does **not** clear the marks — they are a pending copy/move batch it is only
  previewing.
- **`_run_editor()` uses `Screen.suspend()`/`resume()`, never `restore()`** — see that
  section under Cross-file invariants. It is the same loan `E` always took.
- **`MD_SUFFIXES` means "the reader", unconditionally.** An earlier pass routed markdown to
  `open -a Warp` behind a `_warp_can_render()` gate, on the belief that Warp's Markdown
  Viewer would render it — inferred from the bundle's `CFBundleDocumentTypes` registration
  and **never actually observed**. The reader retires the question by not asking it: there is
  no `TERM_PROGRAM` test, no platform split and no dependency on what happens to be
  installed, which is what makes this a routing rule rather than an integration. Do not
  reintroduce a viewer handoff for `.md`; a link cannot be followed from another app's tab.
- **The routing tables are not config.** `DEFAULTS`, `DEFAULT_CONFIG_TEXT` and the README
  table move together by the rule above; keeping the extension lists as module constants is
  what keeps all three out of this change.
- **`↵` stays a fixed key.** No new bindable action, so `RESERVED_KEYS` and the
  `set(DEFAULT_KEYS)`-vs-`run()` set equality are unchanged — the reader's keys are all
  panel-local — but `KEY_SECTIONS`' `↵` row and `_hint_line()`'s `"↵ read/open/cd"` segment
  are both descriptions of behaviour that just moved, and the README key table with them.

## The markdown reader

The sixth panel mode, and the only one whose body is a *document*. It exists because a
markdown viewer in another application cannot follow a link back into the tree — and because
the vault this was written for uses plain relative links (`[x](notions/y.md)`, 1239 of them)
rather than wikilinks (2 files), so following one is path resolution against the containing
file and needs no index, no vault and no external tool. **Obsidian is not involved**: it is
Electron and cannot be embedded, and its CLI resolves paths relative to one vault, so
anything built on it is silently useless on `navigateur`'s own `README.md`.

- **Parse once, render per width. That split is the feature's spine.** `md_parse()` assigns
  every link an id — its index in the returned list, in document order — and `md_render()`
  only wraps. A rendered *line number* is a function of the pane, so a `SIGWINCH` mid-read
  would move a selection stored as one. `self.reader.link` is therefore an id, and the
  renderer (the only thing that knows the width) resolves it through `linemap`. `blockmap`
  does the same job for an anchor. Store a line number anywhere in reader state and the bug
  comes back, silently, only on resize.
- **`dwidth()`, never `len()`, everywhere a column is counted.** CLAUDE.md already warned
  that a double-width glyph tears the box; the reader is what made it a daily event (75 of
  those 179 files carry emoji). `_panel()`'s gap-padding and truncation now use it too, which
  fixes every panel, not just this one. Ambiguous-width (`A`) — which is what *every*
  box-drawing character reports — is deliberately counted as **1**: correct outside a CJK
  locale, and the one assumption here that would break inside one.
- **The plain and styled halves must have identical trailing whitespace.** `_panel()` sizes
  the right-hand gap from `dwidth(plain)` alone, so a pad that survives only in the styled
  half walks the border out by exactly that many cells. `_md_paint()` strips trailing space
  tokens from both, and `_md_cell()` skips the last column's padding for the same reason.
  A vault-wide sweep asserting `plain == styled` when no styles are passed is what catches it.
- **`_md_clip()` clips *spans*, not joined text.** Cutting the joined string would throw the
  link ids away with the span boundaries, so a table narrow enough to clip would silently
  lose the links on that row. `_md_cell()` also reports the cell's **original** ids: a link
  whose label got cut off is still on that row, and `linemap`'s job is to name a row to
  scroll to, not to certify that the label survived.
- **`_md_paint()` coalesces neighbouring same-style tokens.** `_md_flow()` splits on spaces,
  so without it every word arrives in its own escape pair — three times the bytes through
  `paint()`'s diff, and no phrase in the output is contiguous enough for anything to match.
- **Anchors are Obsidian's rule, not GitHub's.** `md_anchor_line()` unquotes and compares
  against the heading's **literal text** (`#Abstract%20class` → `Abstract class` → `##
  Abstract class`). GitHub's lowercase-and-hyphenate slug matches none of this vault's 367
  same-file anchors. Verified 97/97 on `GLOSSAIRE.md`; case-insensitive containment is the
  fallback, not the rule.
- **A same-file `#anchor` pushes no history.** It is a scroll, not a document change, and 367
  of them would make `⌫` useless.
- **`_panel_content()` takes a width; its second caller has none.** `_panel_key()` calls it
  for `len(body)` to clamp — CLAUDE.md names that sharing on purpose — and the reader is the
  one panel whose body length depends on the pane. `_panel()` therefore caches `panel_w` and
  `panel_h` at paint time and the handler reads those: a keypress always follows a paint.
  They are seeded in `__init__` so that is a nicety, not a correctness argument.
- **`_panel_key()` takes `screen`.** `E` inside the reader hands the real terminal to a
  foreground editor, which needs `suspend()`/`resume()`. There is exactly one call site, in
  `run()`, so threading it beat the alternative of handling one panel's key outside the
  panel handler.
- **`b"[Z"` had to join `key()`'s escape table.** Shift-Tab is CSI Z; undecoded it falls
  through to `"ESC"`, so walking links backwards would have closed the reader.
- **`esc` closes, `q` quits — the reader does not get the pager convention.** It is in
  neither `SUBMENUS` nor `CURSOR_PANELS`, so `esc` goes straight to the tree and drops the
  history with it. Back is `⌫` (and `h`/`←`, which mean "leave" in the tree and "back" here —
  different input paths, no conflict).
- **Every failure keeps you where you are.** `open_reader()` returns False with a message for
  an unreadable file, and a link to something missing leaves the document on screen. Same
  rule as `open_it()`/`spawn_terminal()`: these keys must never be the ones that raise out of
  the browser.
- **The markdown layer is pure and is the only part testable without a TTY.** `md_parse` /
  `md_render` / `md_anchor_line` / `dwidth` are `str,list -> list`. The check that matters is
  a sweep of every `.md` in the vault at several widths asserting three things at once: no
  line exceeds the width by `dwidth`, `plain == styled` with no styles, and **every link id
  appears in `linemap`** — that last one is what caught `_md_cell` eating ids.

## The keymap and the panels

`KEY_SECTIONS` (`src/nav.py`) is **one table read by two consumers**: the `?` panel prints
it, and `run()` dispatches on the actions in it. `run()` resolves `action =
self.binds.get(key)` and branches on the *action*, never on a literal character — that is
the whole point. A key literal in the dispatch is a binding the `?` table cannot know
about, and a row in the table with no matching branch is a lie printed on screen. The
cheapest check is set equality between the two — `set(DEFAULT_KEYS)` against the actions
`run()` actually branches on — which is worth re-running by hand after touching either.

There is no `VERB_KEYS` any more, and no confirm step keyed off `↵`: the verb key
(`op_move`/`op_copy`/`op_cut`/`op_delete`) *is* the trigger now — it calls
`try_op()`/`try_delete()` the instant it is pressed, on whatever `self.marked` holds. Every
place that names a verb key to the user — `_hint_line()`'s `self.marked` branch,
`toggle_mark()`'s message, `try_op()`'s confirm prompt — reads it live from `self.keys`,
because `m`/`c`/`x`/`d` and `e` can all be rebound. Nothing spells them as literals.

- **Rows with `action = None` are the fixed keys** — `↵`, `esc`, `q`, `^c`/`^d`. They are
  displayed but never rebindable, and `RESERVED_KEYS` refuses them (plus the digits, which
  `B`/`b` read as bookmark slots **and** `count_buf` reads as a repeat count — two
  independent reasons, so removing either one does not free the digits). This is `load_config()`'s rule applied to keys: a typo
  must never cost you the browser you would use to fix it, so the way *out* is never
  something the user can spell wrong. Arrows are in `ARROW_KEYS`, applied over the user's
  bindings in `_build_binds()`, so rebinding `down` cannot steal `↓`.
- **There are six panel modes**: `keys`, `settings`, `colors`, `binds`, `bookmarks` and
  `reader`. The reader is the only one with a body that depends on the pane width; see its
  own section.
- **A panel is a mode, not an overlay.** `Screen.paint()` diffs against `prev`, so anything
  written outside `frame()`'s return value is clobbered on the next changed line. `frame()`
  dispatches to `panel_frame()` on `self.mode` and `run()` routes to `_panel_key()` — one
  paint path, one input path. `read_slot()` is modal *input* only (a single blocking read);
  a panel needs a loop, which is why it is a mode instead.
- **`_panel_key()` is called above the `count_buf` block in `run()`**, because that block
  eats every digit and the bookmarks panel *is* indexed by digit: `_panel_key()` has an
  explicit `mode == "bookmarks" and key in "0123456789"` branch that jumps to the slot,
  reusing `b`'s idiom now that the slots are on screen. Move the call below `count_buf` and
  that branch becomes unreachable rather than wrong — nothing fails, the digits just stop
  working.
- **`q` quits from inside a panel; `esc` closes it.** Deliberately not the pager
  convention. `q` is the one key in this file with no second meaning anywhere, and `ESC`
  already carries the context-sensitive one (cancel a pending op / back out a level).
- **Panels with a cursor keep flat bodies** — no headings, no blank rows — so
  `panel_cursor` is a plain index into the body list. `keys` is the one grouped panel and
  the one with no cursor. `_panel_content()` is shared by the renderer and the key handler
  so the cursor can never run past the end of a body only the renderer measured.
- **Body builders return `(plain, styled)` pairs.** The plain half is what truncation and
  gap-padding measure — `render_row()`'s trick — and being pure `-> list[...]` is what
  makes them exercisable without a TTY, which `nav.py` otherwise refuses to run without.
- **The footer stays ONE line.** `frame()`'s `body_h = max(3, height - 4)` and
  `_scroll_into_view()` both assume exactly one hint line, and `measure()` clamps to 40x8 —
  three body rows. A permanent multi-line legend would have to renegotiate both. That is
  why the table lives behind `?`. `_hint_line()`'s segment list is ordered for its own
  truncation rule (it drops the *second-to-last* segment first), so `? keys` sits near the
  front: the surviving segments at 40 cols are the front of the list plus `q quit`.

### Writing config.toml

The settings panel is the **first code that writes user configuration**, and
`write_config_value()` exists in the shape it does because of one rule: **patch the single
line, never regenerate the file.** `DEFAULT_CONFIG_TEXT` is written only on first run, so
by the time anything edits the file it holds the user's comments, their column alignment,
and possibly keys this version has never heard of. Rebuilding it from the template to
change one colour would silently delete all three. There is no stdlib TOML writer and that
is not a reason to hand-roll a serialiser.

- **`_trailing_comment()` skips the quoted value before it looks for `#`.** Every colour in
  this file *is* a `"#rrggbb"` string, so a bare `rest.index("#")` cuts one in half and
  turns the value into a comment.
- **`_save_config()` writes a temp file and `os.replace`s it.** A config truncated by a
  crash mid-write takes the colours with it, and the browser reads this file before it can
  draw the screen you would fix it from.
- **`key_ok()` is the one validator**, shared by `load_config()` and the panel — the same
  class of rule as `group_ok()`. A collision is *refused*, never resolved: two actions on
  one key would otherwise be settled silently by the order of `run()`'s elif chain. In
  `load_config()` a bad binding degrades to its default key by key, matching how every
  other setting there behaves.
- **`DEFAULTS["keys"]` and the `[keys]` half of `DEFAULT_CONFIG_TEXT` are generated from
  `KEY_SECTIONS`**, not written out by hand. CLAUDE.md's rule is that `DEFAULTS`,
  `DEFAULT_CONFIG_TEXT` and the README table change together; generating two of the three
  is how that stops depending on anyone remembering.
- **A rebind rebuilds `self.binds` rather than patching it**, so the old key stops working
  in the same keystroke. Colours are applied by mutating `self.colors`, which is the dict
  every `fg()`/`bg()` call already reads — nothing to relaunch, and the panel is honest
  about the difference if the *write* fails but the in-memory change stuck.
- **An armed edit row swallows the keypress before any panel navigation** — otherwise
  `j`/`k`/`↵` would move the panel's cursor instead of landing in the edit. `^c`/`^d` are
  checked above even that. **`q` is excepted out of the bind branch by hand**, and the
  exception is load-bearing rather than tidy-up: `q` is in `RESERVED_KEYS`, so `_rebind()`
  can only ever *refuse* it — swallowing it bought nothing and quietly cost the
  `q`-always-quits invariant the README states with no exception. The colour prompt keeps
  the swallow (`self.panel_edit != "color"`), because there `q` is a character being typed
  into a text field, not a key being pressed. An earlier version of this bullet claimed the
  swallow was "the only reason `q` can be bound to something", which was never true and is
  exactly the kind of stated-but-false rationale this file exists to prevent.
- **Without `tomllib`, saving is a lie the panel has to admit.** `load_config()` returns
  early on python < 3.11, so the file is never read back — but `write_config_value()` still
  succeeds, and a bare "dir is now #83a598" would promise a setting that is gone at the next
  launch. `SESSION_ONLY` (module level, next to the import that causes it) is appended to
  both success messages and is the empty string everywhere else. This is the same
  degrade-honestly rule as the rest of `load_config()`, applied to a message instead of a
  value.

## Cross-file invariants

Each of these looks like removable noise and is load-bearing:

- **TTY name symmetry.** `_nav_tty()` (`src/nav.zsh`) and `self_tty()` (`src/nav.py`)
  must produce byte-identical strings — that one string is the FIFO stem, the self-exclusion
  key in `publish()`, *and* the `roles/<tty>` filename. Diverge and the driving terminal
  silently fights its own `cd`, or takes a role nobody can see. The rule is **the path under
  `/dev` with `/` mapped to `-`**, not a basename: a basename collapses Linux `/dev/pts/3` to
  `3`, colliding with the console `/dev/tty3`. macOS `/dev/ttys003` has no slash left to map,
  so the rule is a no-op there and pre-existing state stays valid.
- **A FIFO payload is one newline-terminated write** (`src/nav.py:1111`). A partial line
  blocks `read -r` inside the `zle -F` callback and freezes that terminal's line editor.
- **`ENXIO` on open means "no reader"** — that terminal died without cleanup, so `publish()`
  unlinks the FIFO. The garbage collection deliberately free-rides on a write we were doing
  anyway, which also means it is **not comprehensive**: it only ever sweeps the group being
  published to, so a leftover in a group nobody is in any more waits until somebody joins and
  moves. That is no worse than the pre-groups "nobody ever publishes again" case; leave it
  alone.
- **Two follow paths, one state.** Live (`zle -F` on the per-tty FIFO, the only way to wake
  an idle zsh from outside) degrades to lazy (`_nav_precmd`, reading
  `$NAV_STATE/groups/<group>/cwd`).
  `_NAV_SEEN` is recorded *before* acting — including for a message deliberately not acted
  on — so the lazy path never drags the user back after they cd'd somewhere themselves.
  `Navigateur.sync_from_group()` is the third reader of that state and obeys the same
  record-before-acting rule with `self.seen`. All three read the *group's* `cwd`. It **polls
  it rather than opening the FIFO**: the shell holds that FIFO open for `zle -F`, and a second reader would race it
  for the bytes, so the message would vanish before the shell could act on it. Its
  "we are already there" test is `published_dir()` **and** expanded-or-root, never
  `published_dir()` alone: a folder under the cursor is collapsed until something expands
  it, so the looser test left the incoming directory merely *selected* — and stuck, since
  every later message naming it took the same exit, `f` included.
- **`add-zsh-hook`, never `precmd_functions=(...)`.** Warp already has entries in that array
  and clobbering it breaks the terminal.
- **Rendering is Warp-shaped.** `Screen.paint()` diffs against the previous frame and
  repaints only changed lines; full clears flicker. `SIGWINCH` and a size change just blank
  `prev` to force a repaint. `measure()` clamps to 40x8 because a terminal reporting 0x0
  mid-resize would otherwise render an empty screen.
- **`SIGWINCH` needs the self-pipe to mean anything.** Blanking `prev` only takes effect at
  the top of `run()`'s loop, and the loop is parked in `os.read()` on the keyboard. PEP 475
  restarts that read once the handler returns normally, so the handler alone left the window
  resized and the frame stale until the user happened to press a key — `key()`'s
  `except InterruptedError: return None` branch was the intended contract and simply never
  fired. `Screen.__enter__` registers `signal.set_wakeup_fd` on a non-blocking pipe and
  `key()` selects on **the keyboard and that pipe**, so a signal returns `None` and the loop
  repaints. Consequences: `key()` now selects even when `timeout is None`; `restore()` must
  `set_wakeup_fd(-1)` *before* closing either end, or a signal arriving afterwards prints
  "Exception ignored" over the restored screen; `warn_on_full_buffer=False` is not optional,
  since the default writes that warning to **stderr, which here is the alt screen** — the
  same rule as "stdout is the display, never a data channel", and a dropped wakeup byte only
  costs one late repaint; and the whole thing is wrapped so a failure degrades to the old
  press-a-key behaviour rather than costing you the browser.
- **`Screen.suspend()`/`resume()` are a loan, not a shutdown.** `edit_it()` (`E`) is the first
  code that hands the real terminal to a foreground child (`nvim`), and `restore()` is the
  wrong tool for giving it back: `restore()` also calls `set_wakeup_fd(-1)` and clears
  `self.saved`, which is correct for a one-time exit but would leave a SIGHUP arriving while
  `nvim` owns the terminal unable to restore afterward. `suspend()`/`resume()` touch only the
  raw-mode/alt-screen/cursor state and deliberately leave the wakeup pipe and signal handlers
  armed the whole time — a `SIGWINCH` firing mid-edit just blanks `prev`, harmless with nothing
  painting. `resume()`'s `termios.tcflush(TCIFLUSH)` is load-bearing, not cosmetic: without it,
  a keystroke queued during `nvim`'s own exit sequence is still sitting in the tty buffer when
  `key()` next reads, and decodes as a real keypress in the browser — a stray `ESC` with
  nothing marked would quit it outright.
- **Both backspaces erase.** The colour prompt accepts `\x7f` **and** `\x08` — which one a
  terminal sends depends on its erase setting, and a typing prompt where the erase key
  silently does nothing is the kind of bug nobody reports and everybody hates.
- **Config degrades key by key.** `load_config()` type-checks each key over `DEFAULTS`; a
  typo must never cost you the browser you would use to fix it. Adding a setting means
  touching `DEFAULTS`, `DEFAULT_CONFIG_TEXT` and the README table together — and note
  `DEFAULT_CONFIG_TEXT` is written only on first run, so for an existing user it is the merge
  against `DEFAULTS`, not the template, that actually supplies the new value.
- **Colours are plain 24-bit hex, not palette slots.** That is the reason this is raw ANSI
  rather than curses. Keep it that way.

## Move/copy/cut/delete: the first code that mutates the browsed tree

Everything else `nav.py` writes is bookkeeping under `$NAV_STATE` — bookmarks, roles, group
`cwd` files. `do_move()`/`do_copy()`/`try_delete()` are the only places the browser changes
what it is showing you, via `shutil.move()`/`shutil.copy2()`/`shutil.copytree()`/
`shutil.rmtree()`/`Path.unlink()`, and the invariants below exist because a mistake here loses
a user's files rather than corrupting a scratch file that a restart repairs.

**`self.marked` is the only op state — a free-standing selection, not a mode.** `e`
(`toggle_mark()`) adds or removes a path string at any time; there is no `pending_op` field
any more. The verb comes from the key you press *after* marking: `run()`'s `op_*` dispatch
maps `op_move`/`op_copy`/`op_cut`/`op_delete` to the string `"move"`/`"copy"`/`"cut"`/
`"delete"` and passes it straight into `try_op(…, verb)` / `try_delete()`, which run the
confirm immediately. `verb` lives for exactly that one keypress — nothing stores it. This is
the inversion of the old flow (arm a mode with `m`, mark with `e`, confirm with `↵`), made
at the user's request: mark first, then act.

- **The verb key is the trigger, and there is no `↵` confirm any more.** `try_op()` /
  `try_delete()` still run their own `y` / per-item prompt via `read_slot()`; what changed is
  that pressing `m`/`c`/`x`/`d` *is* what reaches them. `↵` is back to two-way (open a file /
  cd on a folder). `VERB_KEYS` is gone with the mode.
- **Nothing marked: all four verbs refuse.** The `op_*` dispatch, with `self.marked` empty,
  sets `"nothing marked -- {mark} marks a row"` and returns — there is no implicit "act on
  the cursor row" batch. The plan asked for one (m/c/x on the highlighted row), but for
  move/copy/cut the destination *is* `published_dir()`, read off that same cursor row: a
  one-item batch taken from the cursor can only ever be `[]` ("already there", a file in its
  own directory) or `None` ("into itself", a folder), so the fallback had two refusals and
  no success path. `d` never had a fallback anyway — `d` then `y` on whatever the cursor
  sits on is unrecoverable. Mark first, always.
- **`toggle_mark()` needs no mode.** It used to refuse outside a `pending_op`; now it only
  guards `row is None`. Marks are path strings, independent of `self.rows` (same convention
  as `expanded`), so collapsing a marked folder never unmarks it, and a mark can live in a
  collapsed subtree — which is why `_edit_targets()` sorts the strings rather than walking
  `self.rows`.
- **`self.marked` is never written to disk**, so quitting the browser mid-selection discards
  it like every other in-memory field.
- **Delete does not share `_op_batch()`/`try_op()`** — it has its own `_delete_batch()`
  (nested-mark collapse only, no destination to check for collisions or nesting-into-itself)
  and `try_delete()` (see below), because there is no destination for a delete to collide
  with or land in.
- **Cut is move**, not a third filesystem operation: `try_op()` routes both `"move"` and
  `"cut"` to `do_move()`, and `do_move()` takes a `verb` parameter used *only* to spell
  "moved" vs "cut" in the outcome message. There is deliberately no `do_cut()`. This was an
  explicit user choice — the first ask was answered by pointing out `m` already does what
  "cut" means (relocate, not duplicate), and the user later asked for `x` as a real key
  anyway, for people who think in cut/paste terms; what they did not ask for, and what would
  be a mistake to build, is a second code path that could drift from move's collision/nesting
  guarantees.
- **Marks outlive a cancel, and are shared across the verbs.** A non-`y` reply to
  `try_op()`'s confirm sets `"{verb} cancelled -- marks kept"` and returns *without* clearing
  `self.marked`, so the user can reposition the cursor or press a different verb without
  re-marking (this is a deliberate change from the old clear-on-cancel, per the user).
  `try_delete()` is the exception — see its bullet. The "already there" (`_op_batch` → `[]`)
  and refusal (`_op_batch` → `None`) paths keep the marks too: every non-success exit of
  `try_op()` now leaves `self.marked` alone, and only an actual `do_move()`/`do_copy()`
  clears it. "Already there" in particular is the reposition case — you are standing in the
  wrong directory — so dropping the marks there was the wrong move.
- **Delete confirms per item, not once for the whole batch.** `try_delete()` loops the batch
  and calls `read_slot()` again for each item: `y` deletes this one and advances, `Y` deletes
  this one and every item still left without asking again, anything else stops the rest of the
  batch (same "anything but y cancels" rule as `try_op()`, just scoped to what's left rather
  than the whole operation, since items already deleted can't be un-deleted by a late cancel).
  This is a deliberate divergence from move/copy/cut's single whole-batch prompt, per an
  explicit user request: a mis-click during a large delete is worse than during a move (nothing
  survives to go clean up), so it should cost one keystroke to catch, not zero — while `Y`
  keeps a genuinely large, deliberate batch from being one keypress per file. **`try_delete()`
  clears `self.marked` on *every* exit path** — completion, `OSError`, ctrl-c, a non-`y`
  reply — deliberately unlike `try_op()`, which keeps the marks on a cancel: by the time
  `try_delete()` returns it has already touched the disk, so a surviving mark could point at
  a path that no longer exists. `_delete_batch()`
  is `_op_batch()`'s nested-mark-collapse step alone, deliberately not a call to `_op_batch()`
  with a fake destination, since delete has no destination and none of `_op_batch()`'s
  collision/lexists checks apply. Symlink handling: `p.is_dir() and not p.is_symlink()` routes
  to `shutil.rmtree()`, else `p.unlink()` — `shutil.rmtree()` itself refuses to operate on a
  symlink, and `rm` semantics (remove the link, never dereference into its target) is the
  correct and expected behavior for an irreversible delete.
- **The collision preflight is all-or-nothing, checked before any `shutil.move`/`shutil.copy2`/
  `shutil.copytree` call runs.** `_op_batch()` walks the whole batch — nested-mark collapse,
  duplicate basenames *within the batch itself* (two marked files that happen to share a name
  once dropped into one destination), then `os.path.lexists()` against the destination — and
  refuses the entire operation if any check fails. It is shared unchanged by all three verbs,
  since none of this logic depends on whether the batch ends up relocated or duplicated. The
  point is that a user who sees "would collide" never has to wonder which of N items actually
  moved before the browser noticed; either everything is validated and then acted on, or
  nothing is. If `do_move()`/`do_copy()` still fail partway (an `OSError` the preflight
  couldn't foresee — permissions, a device disappearing), the message says "moved/cut/copied N
  of M" honestly rather than implying atomicity it can no longer promise. A `copytree` that
  fails partway is the messier case of the two: it can leave a partially-populated directory at
  the destination, so "copied N of M" understates what actually needs cleaning up there, unlike
  a failed `shutil.move` (nothing partial to leave behind per item) or a failed `copy2` (the one
  file either copied or didn't).
- **`shutil.move()`, never `os.rename()`, and always passed the destination directory, not
  the joined path.** Passing the directory lets `shutil.move` resolve the join itself; joining
  it yourself risks falling through to a raw rename that silently replaces whatever the
  preflight above just checked for. `shutil.move` also degrades across filesystems, which a
  bare rename does not.
- **`shutil.copytree` needs the joined destination path; `shutil.copy2` needs the bare
  directory — `do_copy()`'s `p.is_dir()` branch exists only because of this asymmetry.**
  Unlike `shutil.move`/`shutil.copy2`, `copytree` refuses an existing target directory rather
  than resolving the join itself (the collision preflight already guarantees the target doesn't
  exist, so that refusal never actually fires — the asymmetry is still real and would silently
  copy into the wrong place if the two calls were made to look the same).
- **Copy's symlink handling intentionally diverges from move's.** `shutil.copytree`'s default
  (`symlinks=False`) and `shutil.copy2`'s default (`follow_symlinks=True`) both dereference
  links, so copying a folder of symlinks yields real copies of their targets — whereas
  `shutil.move` relocates a symlink as a symlink. This is the stdlib default, left as-is rather
  than passed `symlinks=True`/`follow_symlinks=False`; if that turns out to be the wrong call,
  it is a deliberate flag flip here, not a bug to rediscover.
- **A destination inside a marked folder is refused, not attempted.** Moving/copying/cutting a
  folder into its own descendant is checked with `Path.is_relative_to` before anything happens,
  and the refusal (`_op_batch` → `None`) keeps `self.marked` intact so the user can just pick a
  different destination instead of re-marking everything.
- **The confirm prompt's `self.message` bypasses `_hint_line()`'s segment-dropping truncation** —
  that logic only applies to the default hint list, so a raw message is just sliced
  `hint[:cols-3]`. The prompt therefore elides the destination path from the **left**
  (`"…" + tail`), the same rule `_title_line()` uses for long paths, specifically so the
  trailing `"y to confirm"` is never what a long path or a long name list pushes off the edge
  of a narrow terminal — right-truncating a safety confirmation is the one truncation bug in
  this file that would actually be dangerous.
- **`q` and `ESC` are no longer the same branch in `run()`.** They were merged
  (`key in ("q", "ESC")`) before marks existed, since both just quit. Now `ESC` is
  context-sensitive: with `self.marked` non-empty it clears the marks, sets
  `"marks cleared"`, and falls through to the next loop iteration instead of returning; with
  nothing marked it still `return`s exactly like `q`. So `esc` is two presses to quit out of
  a selection — drop the marks, then quit — which the README documents as behaviour. `q`
  itself keeps its own unconditional branch — it must always quit, marks or not, since it's
  the one key with no double meaning anywhere else in the file. Don't refold these back into
  one `elif`.
- **`ENTER` is two-way, keyed on the row, not on any op state.** A file goes to
  `open_file()` and falls through to `maybe_publish()` (the reader is a mode — the browser
  keeps running behind it, so this branch must not `return`); a folder — or a `root_selected`
  teleport, see "Opening a file with `↵`" above — keeps the old unconditional
  `self.chosen = self.published_dir(); return`. There is no pending-op arm any more: the
  verb keys run their own confirm (see the top of this section), so `↵` has nothing to
  confirm. `VERB_KEYS` (a property that mapped verb → key so a hint could name the confirm
  key) is gone with it.

## Scope

The README lists fuzzy search, git decorations and a bash integration as deliberate
omissions; the markdown reader was added after that list was written.

`start.txt` is the original user request. It explains *why* the bindings are what they are,
but it is **not** the spec of record — the README key table is authoritative where the two
differ. `start.txt` asks for "`h` to do `..`", while `leave()` (`src/nav.py:1609`) is
collapse → jump-to-parent → re-root; `O`/reveal is not in the request at all. Do not "fix"
the implementation back toward `start.txt`.

README's "Not in this first pass" lists deliberate omissions (fuzzy search, file operations,
git decorations, a bash integration, following across machines).

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

Grouping is **per machine** — `$NAV_STATE` is a filesystem seam, so a Mac terminal cannot
share a group with one on a remote box. Two ssh sessions into the same host do follow each
other.
