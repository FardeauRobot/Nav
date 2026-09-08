# navigateur

A file explorer that lives in your terminal. An expanding tree like the VSCode
sidebar, `hjkl` to move, colours you set in hex — and terminals that can join a
**group** and all move together, wherever any one of them goes.

```
╭─ ~/Desktop/navigateur   3 ──────────────╮
│   ▾ src/                                │
│ ❯     nav.py                            │
│       nav.zsh                           │
│     README.md                           │
│     start.txt                           │
╰─────────────────────────────────────────╯
  hjkl move · ? keys · ↵ read/open/cd · q quit
```

The badge after the path — `3` above — is the group this window is in; solo
windows and the `default` group show nothing there.

Stdlib-only Python 3 + zsh. No dependencies, no build step. macOS and Linux,
including over ssh.

---

## What you can do with it

### 1. Browse your files and land there

Type `navigate` in any terminal. Move with `j`/`k`, open a folder with `l`,
back out with `h`. Press `↵` **on a folder** and the browser quits **and your
shell is now in that directory**. Press `q` instead and your shell stays where
it was. Press `↵` **on a file** and it opens instead — see below — and the
browser stays open behind it.

Because `↵` on a file now opens it, cd-ing to the folder a file *lives in* means
highlighting that folder, or pressing `h` first.

That is the core of it: a visual `cd`. Give it a starting point with
`navigate ~/projects`, and jump around with bookmarks — `B` then a digit to
save the folder you're on, `b` then that digit to come back, from any window.
(On a file, `B` saves its folder — the same rule `↵` uses.)

### 2. Opening a file with `↵`

`↵` on a **folder** is the visual `cd` above. `↵` on a **file** opens it and
leaves the browser running, routing on the file's extension:

| the file | what happens |
| --- | --- |
| `.md`, `.markdown`, `.mdown` | the **reader**, right there in the browser — see below |
| any source or config file — `.c` `.h` `.cpp` `.py` `.rs` `.go` `.js` `.ts` `.sh` `.zsh` `.toml` `.json` `.yaml` `.txt` and friends, plus `Makefile`, `Dockerfile`, `.zshrc`, `.gitignore`… | your editor, in this terminal — the same handoff `E` uses |
| anything else — a `.png`, a `.pdf`, a binary | your desktop's default app, exactly as `o` does |

The editor is `$VISUAL`, then `$EDITOR`, then `nvim`, then `vim`. A value with
arguments (`code -w`) works; a GUI editor that returns immediately will just
flicker the screen, so give it its wait flag.

`E` in the tree opens every **marked** file at once (mark with `e`) — under
`nvim`/`vim` they open in split windows (`-o`), any other editor just gets the
file list. With nothing marked, `E` edits the highlighted row. `E` never clears
your marks, so you can preview a batch and then still `m`/`c` it.

### 3. Reading markdown, and following its links

`↵` on a `.md` opens it **inside the browser**. Headings, bold, `code`, lists,
task lists, block quotes, tables and fenced code blocks are rendered; YAML
frontmatter is skipped; the page re-wraps when you resize the window.

Links are the point. `Tab` walks them, `↵` follows the one you're on:

| key | in the reader |
|---|---|
| `j` / `k`, `↓` / `↑` | scroll a line |
| `Space` / `b` | page down / up |
| `g` / `G` | top / bottom |
| `Tab` / `Shift-Tab` (or `n` / `p`) | next / previous link |
| `↵` | follow the selected link |
| `⌫` (or `h` / `←`) | back to the page you came from, at the line and the link you left |
| `E` | edit this file in your editor, then re-read it |
| `Esc` | close the reader |
| `q` | quit the browser, as everywhere else |

A link is resolved **relative to the file it is written in**, so
`[notions/](notions/INDEX.md)` in `cpp/INDEX.md` goes exactly where it would in
any markdown tool. What happens next depends on what it points at:

| the target | what happens |
|---|---|
| another `.md` | opens in the reader; `⌫` comes back |
| `#a heading` in this file | scrolls there — it isn't a new page, so it doesn't join the back history |
| `other.md#a heading` | opens that file, scrolled to that heading |
| a folder | closes the reader and jumps the tree there |
| a source or config file | your editor, then back to the document |
| `http://…`, `https://…` | your browser, via the desktop |
| something that isn't there any more | says so, and leaves you on the page you were reading |

There is no vault, no index and no external tool: a `.md` anywhere on disk
reads the same way, this project's own `README.md` included.

### 4. Hand a file or folder to the rest of your desktop

Four keys reach outside the terminal, using whatever is under the cursor:

- `o` — open it with its default app (a folder opens in your file manager)
- `O` — reveal it in the file manager, with the file selected
- `w` — open this folder in a **new terminal window**
- `t` — open this folder in a **new terminal tab**

For `w` and `t`, "this folder" means the nearest enclosing directory — on a
file you get its folder, not an error. Which terminals this works with is the
one genuinely uneven part of the tool; see [Limitations](#limitations).

### 5. Join a group and move together

Windows that share a **group** all sit in the same directory. There is no
leader: whenever *any* member `cd`s — by hand, or with `↵` in the browser —
every other member follows.

```sh
navigate 3          # join group 3; run it again to leave
navigate 7          # switch this window to group 7
navigate solo       # leave whatever group this window is in
navigate status     # every group, its members, its live count
```

`navigate 3` and `navigate 7` are shortcuts for `navigate group 3` /
`navigate group 7`; a name works too — `navigate group work`. A group name is
up to 32 characters of letters, digits, `-` and `_`. Running the join for the
group you are already in **leaves** it, so the digit is a toggle. (A directory
literally named `3` is still browsable as `navigate ./3` — a bare run of digits
is always read as a group.)

Joining adopts the group's current directory. From then on every member's move
pulls the rest along, **live while you browse**: a member that is itself
running `navigate` doesn't just move its shell — its tree expands out to the
new directory while you watch.

While you browse, "where the group is" is the highlighted row's nearest
enclosing directory, so arrowing between two files in one folder moves nobody.
That is a preview: quitting with **`q` snaps the others back** to your shell's
real directory, while `↵` on a folder takes the whole group there.

Membership belongs to the *window*: it survives quitting the browser and lasts
until you leave the group or close the terminal. Groups are independent — group
`3` can't see group `7`, each has its own members and its own broadcasts — and
a window is in one group at a time.

If two members `cd` at the very same moment, before either shell has drawn its
next prompt, there is no tie-breaker: each ends up where it put itself and the
group is split until somebody moves again.

---

## Install

```sh
git clone https://github.com/FardeauRobot/Nav.git ~/navigateur
cd ~/navigateur && ./install.sh          # or: sh install.sh
exec zsh
```

(`git@github.com:FardeauRobot/Nav.git` if you have ssh keys set up.)

Then type `navigate` in any terminal, and `q` to quit. `nav` and `n` are
shorter aliases for the same thing.

`install.sh` copies nothing and builds nothing — `src/nav.zsh` works out its
own location when sourced, so installing is **one line appended to your
`.zshrc`** (`$ZDOTDIR/.zshrc` if you set that). Re-running is a no-op.

| flag | |
|---|---|
| *(none)* | append the `source` line |
| `--dry-run` | show the line it would append, write nothing |
| `--uninstall` | take the line back out (works even without zsh or python3 installed) |
| `-h`, `--help` | usage |

Your colours and state in `~/.navigateur/` are never touched, `--uninstall`
included.

It refuses to install without **zsh** or **python3**, and warns — without
stopping — on a python3 older than 3.11.

**Keep the clone where you cloned it.** If you move or rename the directory
afterwards, the line in your `.zshrc` points at a path that no longer exists.
Re-running `install.sh` notices and tells you, but it adds a fresh correct line
*next to* the dead one rather than repairing it — delete the old line by hand.

`nav.zsh` must be **sourced, not executed**: `navigate` has to be a shell
function, because no process can change its parent shell's directory. That is
what the `source` line in your `.zshrc` is for.

---

## Keys

| key | does |
|---|---|
| `j` / `k` | down / up (`↓` `↑` too) |
| `l` | expand the folder (`→` too) |
| `h` | collapse it — or jump to the parent — or, at the top, re-root one level up (`..`) (`←` too) |
| `=` | collapse every expanded folder, back to the root listing |
| `g` / `G` | top / bottom |
| `.` | show/hide dotfiles |
| `o` | open with the default app; a folder opens in the file manager |
| `O` | reveal in the file manager (parent folder, file selected) |
| `E` | edit the highlighted file or folder in `$VISUAL`/`$EDITOR` (else `nvim`, else `vim`), taking over the terminal |
| `w` | open this folder in a new terminal **window** |
| `t` | open this folder in a new terminal **tab** |
| `B` then `0`-`9` | bookmark the folder you're on under that digit |
| `b` then `0`-`9` | go to that bookmark |
| `n` | create a new empty file here, after typing a name |
| `r` | rename the highlighted file or folder: edit the name (←/→, Ctrl-A/E/W/U/K), then `↵`, then `y` to confirm |
| `m` | move everything marked into the current folder (asks `y` once); does nothing if nothing is marked |
| `c` | copy everything marked into the current folder (asks `y` once); does nothing if nothing is marked |
| `x` | cut everything marked into the current folder — the same operation as move, under its own key; does nothing if nothing is marked |
| `d` | delete everything marked, asking `y`/`N` per item (`Y` = all the rest); does nothing if nothing is marked |
| `e` | mark/unmark the current row — any time, no mode |
| `f` | **join or leave this window's group** — joins `default` when solo, rejoins the last group you left, or leaves the one you're in |
| `?` | the full key table, laid out and grouped |
| `,` | settings: your colours, your keys, your bookmarks |
| `↵` | **on a folder**: quit and cd your shell here. **On a `.md`**: read it in the browser (see *Reading markdown*). **On any other file**: open it (see *Opening a file with `↵`*), staying in the browser |
| `q` or `Esc` | quit, leaving your shell where it was — `Esc` instead drops your marks if you have any, or closes a panel, without quitting |
| `Ctrl-C` / `Ctrl-D` | quit, same as `q` |

The line at the bottom of the window is deliberately short — five keys, not
sixteen — because on a narrow terminal a long one just loses its tail without
telling you. `?` is the whole list, and it is the segment that survives
longest when the window gets small.

### `?` and `,` — the panels

`?` opens the key table above, grouped and aligned, and reflecting your actual
bindings rather than the defaults. `,` opens a small menu:

| panel | shows |
|---|---|
| colours | every colour in `config.toml`, its hex, and a swatch in that colour — `↵` to type a new one |
| keybinds | every rebindable action and the key it's on — `↵`, then press a key |
| bookmarks | all ten slots, where each points, and which ones are dead |

The reader (above) is a panel too, which is why `Esc` closes it and `q` still
quits from inside it.

Inside a panel: `j`/`k` move, `↵` opens the row (in **bookmarks**, `↵` goes
there and closes the panel), `Esc` backs out one level — submenu → menu →
browser. **`q` still quits the browser outright, from inside a panel as
everywhere else**; it is the one key here that never means anything else.

`f` toggles: press it in a grouped window to go solo, press it again to rejoin
the group you just left. A solo window that has never joined one gets
`default`. It takes no argument — it acts on the group this window is already
in, or the last one it was in.

Mark first, then act. `e` marks or unmarks the current row at any time — no
mode to enter — and you can navigate freely between marks, expanding,
collapsing and jumping around; the marks follow the files, not the rows on
screen. Then press the verb: `m` moves the marked set into the folder under
the cursor, `c` copies it, `x` cuts it (same as move), `d` deletes it. Marks
aren't tied to a verb — mark a batch, then decide whether you want `m`, `c`,
`x` or `d`.

The move, copy and cut confirm prompts name the destination and the files and
ask `y` once for the whole batch. Anything other than `y` cancels — but
**keeps your marks**, so you can pick a different destination or a different
verb without re-marking (`Ctrl-C`/`Ctrl-D` still quit). A destination inside a
folder you've marked, or one that already holds something by the same name, is
refused the same way, marks intact. All four verbs need a mark: with nothing
marked they just say so and do nothing — for `m`/`c`/`x` the destination is
the row under the cursor, so a one-item batch taken from that same row could
only ever be "already there" or "into itself".

Delete confirms differently, since there's no destination to protect: `d` asks
`y`/`N` once **per marked item**, not once for the whole batch — deleting a
file you didn't mean to is worse than a stray move, so each one gets its own
chance to say no. Press `Y` instead of `y` on any item to delete it and
everything still left in the batch without asking again. Anything other than
`y`/`Y` stops there.

Cut and move do the same thing to your files (relocate rather than duplicate);
`x` exists as its own key/label for people who think in cut-and-paste terms,
not because it behaves differently from `m`.

`Esc` drops your marks without touching anything, then (pressed again, with
nothing marked) quits — the same key, two steps.

Unbound keys do nothing — **except escape sequences**: only the four arrows are
decoded, so PageUp, Home, End, the function keys and modified arrows all arrive
as `Esc` and quit the browser.

Bookmarks are ten shared slots. They are **not** per-terminal like group
membership — set one in any window, and every window (and every future
session) can jump to it.

---

## Commands

All three names are the same function: `navigate`, `nav`, `n`.

| command | does |
|---|---|
| `navigate` | open the browser here |
| `navigate ~/projects` | open it rooted there |
| `navigate 3` | join group `3` — toggles out if this window is already in it |
| `navigate group work` | join the group named `work` — same toggle |
| `navigate solo` | leave whatever group this window is in |
| `navigate status [group]` | every group, its members, its live count |

`navigate 3` is shorthand for `navigate group 3`; only a bare run of digits
gets the shorthand, so `navigate ./3` still browses a directory named `3`.

Each of these prints what the window ended up as, including whether it got
**live** or **lazy** delivery:

```
navigateur: in group default (ttys003) · live
navigateur: in group 3 (ttys004) · lazy (moves on your next prompt)
navigateur: solo (ttys005)
```

There is no leader and nothing to refuse: joining a group always succeeds and
the window adopts the group's current directory (or seeds it, if nobody has
moved yet). Every member both broadcasts and follows.

A group name is up to 32 characters of letters, digits, `-` and `_` — it becomes
a directory under `~/.navigateur/groups/`, so `navigate group ../elsewhere` is
refused. Joining a group is all it takes to create one; nothing removes one, so
a group that has broadcast once keeps appearing in `navigate status` with `0
member(s)` after everybody has left.

---

## Limitations

### zsh is a hard requirement, not a preference

Live following needs `zle -F`, which only zsh has — there is no way to wake an
idle bash from outside. A bash port would lose the feature quietly rather than
fail loudly, which is why there isn't one.

```sh
sudo dnf install zsh          # Fedora/RHEL
sudo apt install zsh          # Debian/Ubuntu
chsh -s /bin/zsh              # optional; or just run `zsh` when you want the browser
```

python3 older than 3.11 has no `tomllib`: everything works, but `config.toml`
is ignored and the built-in colours apply. The settings panel still opens and
still edits — the change is just real for that session only, and it says so
rather than claiming a save you would lose at the next launch.

### `w` and `t` — which terminals can open a window or a tab

On **macOS** this is read from `$TERM_PROGRAM`:

| terminal | `w` (window) | `t` (tab) |
|---|---|---|
| Warp | ✓ `warp://action/new_window` | ✓ `warp://action/new_tab` |
| iTerm2 | AppleScript — **untested, no iTerm on this machine** | AppleScript — **untested** |
| Terminal.app | ✓ AppleScript `do script` | ✗ no scriptable new tab |
| anything else | ✗ | ✗ |

On **Linux** there is no `$TERM_PROGRAM`, so the first *installed* terminal
wins — `$NAV_TERMINAL` if you set it, else gnome-terminal, konsole,
xfce4-terminal, kitty, alacritty, foot in that order:

| terminal | `w` | `t` |
|---|---|---|
| gnome-terminal, konsole, xfce4-terminal | ✓ | ✓ |
| kitty, alacritty, foot | ✓ | ✗ no new-tab flag |
| `$NAV_TERMINAL` naming one of the six above | ✓ | as that terminal's row |
| `$NAV_TERMINAL` naming anything else | ✓ launched with this directory as its cwd | ✗ |

`$NAV_TERMINAL` may carry arguments (`"kitty --single-instance"`); the first
word has to be on `PATH` or you get `$NAV_TERMINAL: <name> not found`.

Unsupported combinations say so on the hint line and do nothing else.

The two Warp deep links are **verified against Warp 0.2026.08.19** rather than
assumed. **Warp has no pane action** — `split_pane`, `new_pane`, `add_pane`
and `split_pane_right` are all rejected, so `t` cannot split the current tab
through the URI scheme.

### `o`, `O`, `w` and `t` all need a desktop

| | macOS | Linux |
|---|---|---|
| `o` | `open` | `xdg-open` |
| `O` | `open -R` | `FileManager1.ShowItems` over D-Bus (Nautilus, Dolphin, Thunar); without `gdbus`, opens the parent folder without the selection |

On Linux all four check `$DISPLAY`/`$WAYLAND_DISPLAY` and say `no display`
when there is none — the usual case over ssh to a server. The browser itself
is unaffected.

### Live vs lazy delivery

| | |
|---|---|
| **live** | the terminal jumps immediately, even sitting idle at a prompt |
| **lazy** | it catches up the next time you press Enter |

Lazy is the automatic fallback when `zle` or the `zsh/system` module isn't
available; it costs immediacy and nothing else. Joining a group tells you which
one that terminal got — including **in Warp, where it is unverified**, since
`zle -F` needs a live prompt and can't be tested from a script.

`navigate status` counts a group's `live` members separately from its total, so
`1 member(s), 0 live` for a lazy window is normal, not a fault.

### Two members moving at once

There is no total order across the group. If two members `cd` in the same
prompt gap — before either shell reconciles — each keeps its own directory and
the group stays split until somebody moves again. Common enough to name, rare
enough in practice to leave as is: one more move reconverges everyone.

### Groups are per machine

The shared state is a directory on disk. Two ssh sessions into the same box
move together, but a terminal on your Mac can never share a group with one on
that box — different filesystems, no shared seam.

### Windows that die without cleaning up

A terminal closed without `navigate solo` cleans itself up on the next
broadcast. One that is *killed* outright leaves its group membership behind
until that terminal name is reused, at which point the stale file is dropped
rather than inherited.

---

## Colours and keys

`~/.navigateur/config.toml`, written on first run — or edited for you by the
`,` panel, which patches the one line it changes and leaves your comments and
spacing alone:

```toml
# navigateur configuration. Colours are plain hex. Edit this file, or press `,`
# in the browser -- it patches the one line it changes and leaves the rest be.

[colors]
accent      = "#fe8019"   # the ❯ caret and the active root
dir         = "#83a598"
file        = "#ebdbb2"
dim         = "#928374"   # hints, counts, disclosure triangles
border      = "#504945"
selected_bg = "#3c3836"

[behavior]
show_hidden   = false
group_default = false   # join the `default` group on every launch

[keys]
down           = "j"     # ... one line per action, see `?`
up             = "k"
```

Plain 24-bit hex, no palette slots. A bad value falls back key by key rather
than failing — a typo shouldn't cost you the browser you'd use to fix it. The
two `[behavior]` keys are type-checked as booleans, so they need a literal
`true` or `false`; `show_hidden = 1` is ignored.

### Rebinding

`,` → **keybinds**, `↵` on a row, then press the key you want. Or edit
`[keys]` by hand. Either way the same rules apply, and a binding that breaks
one falls back to its default rather than taking the section down with it:

- One printable character. Not `q`, `Esc`, `↵`, `Ctrl-C` or `Ctrl-D` — the
  ways *out* of the browser are deliberately not yours to misspell — and not a
  digit, because `B` and `b` read one of those as a bookmark slot.
- No two actions on the same key. A collision is refused outright rather than
  quietly settled by whichever branch happens to be checked first.
- The arrow keys always work, whatever `down`/`up`/`enter`/`leave` are bound
  to. Rebinding `j` cannot cost you `↓`.

Colours are editable the same way (`,` → **colours**, `↵`, type `#rrggbb`) and
apply immediately — no relaunch.

`group_default = true` opens every browser session already in the `default`
group, as if you had pressed `f` on launch. A window that is already in a group
keeps that group. (This key was called `follow_default` before groups went
leaderless; an old `follow_default = true` is silently ignored — set
`group_default` instead.)

---

## Files and state

| path | |
|---|---|
| `install.sh` | writes the `source` line into your `.zshrc`; `--uninstall` removes it |
| `src/nav.py` | the browser |
| `src/nav.zsh` | the `nav()` function, group subscription, prompt hooks |
| `~/.navigateur/` | `config.toml`, `roles/<tty>` (this window's group), `groups/<group>/{cwd,gen,sub/<tty>.fifo}`, `bookmarks/<digit>` |

Set `$NAV_STATE` before sourcing `nav.zsh` to move all of that elsewhere —
useful for keeping two installs from sharing groups and bookmarks.
Deleting `~/.navigateur` returns everything to first-run state.
`$NAV_TERMINAL` overrides terminal detection on Linux (see above).

**Upgrading from leader/follower:** a `roles/<tty>` file that still says
`leader 3` or `follower default` is read as plain membership of that group —
the role word is dropped — so a fresh shell picks the group up on its first
prompt and starts moving with it, no action needed. A window that was a *live
follower* and only re-`source`s `nav.zsh` mid-session (rather than starting a
new shell) may stay lazy until you run `navigate <group>` once. The same nudge
covers the older groups upgrade, where the broadcast files first moved under
`groups/`.

---

## Not in this first pass

Fuzzy search, git-status decorations, a bash integration, and
following across machines.
