# navigateur

A file explorer that lives in your terminal. An expanding tree like the VSCode
sidebar, `hjkl` to move, colours you set in hex — and one window can take the
lead and drag your *other* terminals along with it.

```
╭─ ~/Desktop/navigateur   leading ────────╮
│   ▾ src/                                │
│ ❯     nav.py                            │
│       nav.zsh                           │
│     README.md                           │
│     start.txt                           │
╰─────────────────────────────────────────╯
  hjkl move · F lead · f follow · q quit
```

Stdlib-only Python 3 + zsh. No dependencies, no build step. macOS and Linux,
including over ssh.

---

## What you can do with it

### 1. Browse your files and land there

Type `navigate` in any terminal. Move with `j`/`k`, open a folder with `l`,
back out with `h`. Press `↵` and the browser quits **and your shell is now in
that directory**. Press `q` instead and your shell stays where it was.

That is the core of it: a visual `cd`. Give it a starting point with
`navigate ~/projects`, and jump around with bookmarks — `B` then a digit to
save the folder you're on, `b` then that digit to come back, from any window.
(On a file, `B` saves its folder — the same rule `↵` uses.)

### 2. Hand a file or folder to the rest of your desktop

Four keys reach outside the terminal, using whatever is under the cursor:

- `o` — open it with its default app (a folder opens in your file manager)
- `O` — reveal it in the file manager, with the file selected
- `w` — open this folder in a **new terminal window**
- `t` — open this folder in a **new terminal tab**

For `w` and `t`, "this folder" means the nearest enclosing directory — on a
file you get its folder, not an error. Which terminals this works with is the
one genuinely uneven part of the tool; see [Limitations](#limitations).

### 3. Make your other terminals follow this one

One window **leads**, the others **follow** its current directory:

```sh
# in the window that should drive:
navigate lead
# in every window that should tag along:
navigate follow
```

From then on the followers go wherever the leader goes — when you `cd` by hand,
when you press `↵` in the browser, and **live while you browse**. A follower
that is itself running `navigate` doesn't just move its shell: its tree expands
out to the leader's directory while you watch.

While you browse, "where the leader is" is the highlighted row's nearest
enclosing directory, so arrowing between two files in one folder moves nobody.
That is a preview: quitting with **`q` snaps the followers back** to the
leader's real directory, while `↵` takes the leader there too.

There is exactly **one leader per group**, and pressing `F` in a second window
takes the lead away from the first — you never demote anyone by hand. The role
belongs to the *window*: it survives quitting the browser and lasts until you
change it or close the terminal.

**Groups** give you several independent sets of this at once. Add a name and
group `3` can't see group `7` — its own leader, its own followers, its own
broadcasts:

```sh
navigate lead 3          # this window leads group 3
navigate follow 3        # this window follows group 3
navigate status          # every group, its leader, its subscriber count
```

Leaving the name off means **the group this window is already in** (`default`
if it has never joined one), so `navigate lead` in a window already leading
group 3 is a no-op rather than a move. You are in one group at a time.

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
| `g` / `G` | top / bottom |
| `.` | show/hide dotfiles |
| `o` | open with the default app; a folder opens in the file manager |
| `O` | reveal in the file manager (parent folder, file selected) |
| `w` | open this folder in a new terminal **window** |
| `t` | open this folder in a new terminal **tab** |
| `B` then `0`-`9` | bookmark the folder you're on under that digit |
| `b` then `0`-`9` | go to that bookmark |
| `m` | enter **move mode**; `m` again moves everything marked to here (asks `y` to confirm) |
| `c` | enter **copy mode**; `c` again copies everything marked to here (asks `y` to confirm) |
| `x` | enter **cut mode**; `x` again relocates everything marked to here (asks `y` to confirm) -- the same operation as move, under its own key |
| `d` | enter **delete mode**; `d` again deletes everything marked, asking `y`/`Y` per item |
| `e` | mark/unmark the current row, while in move, copy, cut or delete mode |
| `F` | **lead** this window's group — its followers track this window |
| `f` | **follow** this window's group — refused if nobody is leading it |
| `?` | the full key table, laid out and grouped |
| `,` | settings: your colours, your keys, your bookmarks |
| `↵` | quit **and** cd your shell here |
| `q` or `Esc` | quit, leaving your shell where it was — `Esc` instead cancels move/copy/cut/delete mode if one is pending, or closes a panel, without quitting |
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

Inside a panel: `j`/`k` move, `↵` opens the row (in **bookmarks**, `↵` goes
there and closes the panel), `Esc` backs out one level — submenu → menu →
browser. **`q` still quits the browser outright, from inside a panel as
everywhere else**; it is the one key here that never means anything else.

`F` and `f` toggle: pressing either again returns the window to solo. They
take no argument, so they act on the group the window is already in — they
never yank you back to `default`.

At the `B`/`b` digit prompt, any key that isn't a digit cancels (`Ctrl-C` and
`Ctrl-D` still quit). The move, copy and cut confirm prompts (the second `m`,
`c` or `x`) work the same way: anything other than `y` cancels the whole
operation and clears your marks (`Ctrl-C`/`Ctrl-D` still quit). You can
navigate freely between marks — expand, collapse, jump around — nothing
happens until that second `m`/`c`/`x` and the `y` that follows it. A
destination that's inside a folder you've marked, or that already has
something by the same name, is refused with your marks intact, so you can
just pick a different destination.

Delete (`d`) confirms differently, since there's no destination to protect:
the second `d` asks `y`/`N` once **per marked item**, not once for the whole
batch — deleting a file you didn't mean to is worse than a stray move, so
each one gets its own chance to say no. Press `Y` instead of `y` on any item
to delete it and everything still left in the batch without asking again, for
a deliberate large batch. Anything other than `y`/`Y` stops there and
cancels the rest of the batch, same rule as move/copy/cut.

Marks are shared across move, copy, cut and delete: pressing a different
verb's key while you already have marks switches the mode without losing
them, so you can mark a batch, glance at `m`'s hint, then decide you actually
wanted `c`, `x` or `d` instead — whichever verb key you press second is what
runs. Cut and move do the same thing to your files (relocate rather than
duplicate); `x` exists as its own key/label for people who think in
cut-and-paste terms, not because it behaves differently from `m`.

`Esc` also gets you out of move/copy/cut/delete mode at any point before the
confirm prompt, dropping your marks without touching anything — a quicker way
out than pressing the verb key again with nothing marked.

Unbound keys do nothing — **except escape sequences**: only the four arrows are
decoded, so PageUp, Home, End, the function keys and modified arrows all arrive
as `Esc` and quit the browser.

Bookmarks are ten shared slots. They are **not** per-terminal like roles — set
one in any window, and every window (and every future session) can jump to it.

---

## Commands

All three names are the same function: `navigate`, `nav`, `n`.

| command | does |
|---|---|
| `navigate` | open the browser here |
| `navigate ~/projects` | open it rooted there |
| `navigate lead [group]` | this window leads (`leader` is an alias) |
| `navigate follow [group]` | this window follows (`follower` is an alias) |
| `navigate solo` | give up whatever role this window had |
| `navigate status [group]` | who leads each group, and how many follow |

The older spellings still work and mean the same thing:
`navigate follow on|off|toggle|status`, with an optional group after them —
`navigate follow on 3`.

Each of these prints what the window ended up as, including whether a follower
got **live** or **lazy** delivery:

```
navigateur: leading group default (-dev-ttys003) · its followers track this window
navigateur: following group default (-dev-ttys004) · live
navigateur: solo (-dev-ttys005)
```

**Following needs somebody to follow.** With no leader for that group,
`navigate follow` changes nothing and exits 2:

```
navigateur: no leader for group default — run `n lead default` in the window that should lead
```

That is deliberate — the alternative is a follower that will never move. `f` in
the browser says the same. A window that is *itself* the leader gets the same
refusal, so it can never leave a group with nobody leading.

A group name is up to 32 characters of letters, digits, `-` and `_` — it becomes
a directory under `~/.navigateur/groups/`, so `navigate lead ../elsewhere` is
refused. Naming a group is all it takes to create one; nothing removes one, so a
group that has broadcast once keeps appearing in `navigate status` as
`leader none` after everybody has left.

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

### Live vs lazy following

| | |
|---|---|
| **live** | the terminal jumps immediately, even sitting idle at a prompt |
| **lazy** | it catches up the next time you press Enter |

Lazy is the automatic fallback when `zle` or the `zsh/system` module isn't
available; it costs immediacy and nothing else. `navigate follow` tells you
which one that terminal got — including **in Warp, where it is unverified**,
since `zle -F` needs a live prompt and can't be tested from a script.

`navigate status` counts only *live* subscribers, so `0 live-subscribed` while
someone is following is normal, not a fault.

### Following is per machine

The shared state is a directory on disk. Two ssh sessions into the same box
follow each other, but a terminal on your Mac can never lead one on that box —
different filesystems, no shared seam.

### Windows that die without cleaning up

A terminal closed without `navigate solo` cleans itself up on the next
broadcast. One that is *killed* outright leaves its role behind until that
terminal name is reused, at which point the stale role is dropped rather than
inherited.

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
show_hidden    = false
follow_default = false   # start this window as the leader

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

`follow_default = true` is the **leader** side despite its name: it opens every
browser session as if you had pressed `F`, taking the lead of `default`. It
makes nothing *follow* — that is still `f` / `navigate follow`, per window — and
a role you have already set wins over it.

---

## Files and state

| path | |
|---|---|
| `install.sh` | writes the `source` line into your `.zshrc`; `--uninstall` removes it |
| `src/nav.py` | the browser |
| `src/nav.zsh` | the `nav()` function, follow subscription, prompt hooks |
| `~/.navigateur/` | `config.toml`, `roles/<tty>`, `groups/<group>/{cwd,sub/<tty>.fifo}`, `bookmarks/<digit>` |

Set `$NAV_STATE` before sourcing `nav.zsh` to move all of that elsewhere —
useful for keeping two installs from sharing roles, groups and bookmarks.
Deleting `~/.navigateur` returns everything to first-run state.
`$NAV_TERMINAL` overrides terminal detection on Linux (see above).

**Upgrading from a version without groups:** your role files still read
correctly (they are taken as group `default`), but the broadcast files moved
under `groups/`, so a window that was **already following** needs one
`navigate follow` to re-subscribe.

---

## Not in this first pass

Fuzzy search, rename, git-status decorations, a bash integration, and
following across machines.
