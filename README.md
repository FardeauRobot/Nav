# navigateur

A file explorer that lives in the terminal. Expanding tree like the VSCode
explorer, `hjkl` keys, hex-configurable colours — and one window can take the
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

## Install

```sh
git clone https://github.com/FardeauRobot/Nav.git ~/navigateur
cd ~/navigateur && ./install.sh          # or: sh install.sh
exec zsh
```

(`git@github.com:FardeauRobot/Nav.git` if you have ssh keys set up.)

`install.sh` copies nothing and builds nothing — `src/nav.zsh` works out its own
location when it is sourced, so installing is one line appended to your
`.zshrc` (`$ZDOTDIR/.zshrc` if you set that). Re-running it is a no-op, and
`./install.sh --uninstall` takes the line back out. `--dry-run` shows you the
line without writing it. Your colours and state in `~/.navigateur/` are never
touched, including by `--uninstall`.

It refuses to install without **zsh** or **python3**, and warns — without
stopping — on a python3 older than 3.11, where `config.toml` is ignored and the
built-in colours apply.

On Fedora (and other distros that ship bash as the login shell), that first
check is the one you'll hit; zsh is a hard requirement, not a preference:

```sh
sudo dnf install zsh          # python3 is already there, and 3.11+ so tomllib is too
chsh -s /bin/zsh              # optional; or just run `zsh` when you want the browser
```

Live following needs `zle -F`, which only zsh has: there is no way to wake an
idle bash from outside, so a bash port would quietly lose the feature rather
than fail loudly. That is why there isn't one.

Then type **`navigate`** in any terminal to open it, and **`q`** to quit.
(`nav` and `n` are shorter aliases for the same thing.)

**It must be sourced, not run.** `navigate` has to be a shell *function*: a process
can never change its parent shell's directory, so a script's `cd` would die
with its own subshell. The browser writes where it landed to a temp file and
the function cds there afterwards — the same trick `ranger` and `lf` use.

## Keys

| key | does |
|---|---|
| `j` / `k` | down / up (arrows work too) |
| `l` | expand the folder |
| `h` | collapse it — or jump to the parent — or, at the top, re-root one level up (`..`) |
| `o` | open with the default app; a folder opens in Finder |
| `O` | reveal in Finder (opens the parent with the file selected) |
| `w` | open this folder in a **new terminal window** |
| `t` | open this folder in a **new terminal tab** |
| `F` | make this window the **leader** — the others follow where it goes |
| `f` | make this window a **follower** of the leader — refused if nobody is leading |
| `.` | show/hide dotfiles |
| `g` / `G` | top / bottom |
| `↵` | quit **and** cd your shell here |
| `q` | quit, leaving your shell where it was |


`w` and `t` use the same "nearest enclosing directory" rule as `↵` and the
followers — on a file you get its folder, not an error. Which terminal they
drive is read from `$TERM_PROGRAM`:

| `$TERM_PROGRAM` | `w` | `t` |
|---|---|---|
| Warp | ✓ `warp://action/new_window` | ✓ `warp://action/new_tab` |
| iTerm2 | AppleScript — *untested, no iTerm on this machine* | AppleScript — *untested* |
| Terminal.app | ✓ AppleScript `do script` | ✗ — has no scriptable new tab |
| anything else | ✗ | ✗ |

On Linux there is no `$TERM_PROGRAM`, so the first installed terminal wins
instead — `$NAV_TERMINAL` if you set it, else gnome-terminal, konsole,
xfce4-terminal, kitty, alacritty, foot in that order:

| terminal | `w` | `t` |
|---|---|---|
| gnome-terminal, konsole, xfce4-terminal | ✓ | ✓ |
| kitty, alacritty, foot | ✓ | ✗ — no new-tab flag |
| `$NAV_TERMINAL` (anything else) | ✓ launched with this directory as its cwd | ✗ |

Unsupported combinations say so on the hint line and do nothing else.

**All four of `o`, `O`, `w` and `t` need a desktop.** On Linux they check
`$DISPLAY`/`$WAYLAND_DISPLAY` and say `no display` when there is none, which is
the usual case over ssh to a server — the browser itself is unaffected. `o` uses
`xdg-open`; `O` uses the freedesktop `FileManager1.ShowItems` D-Bus call (Nautilus,
Dolphin, Thunar), falling back to opening the parent folder without the selection
when `gdbus` is missing.

The two Warp deep links are verified against Warp 0.2026.08.19 rather than
assumed: they log `root_view:add_session_at_path` and
`root_view:open_new_from_path` in `~/Library/Logs/warp.log`, where an action
Warp doesn't know logs `Received "action" intent with unexpected action`
instead. **Warp has no pane action** — `split_pane`, `new_pane`, `add_pane` and
`split_pane_right` are all rejected that way, so `t` cannot split the current
tab through the URI scheme.

## Leader and followers

Windows have a **role**, the way decks in a DJ booth have a master: one window
**leads**, the others **follow** its current directory. Set the role from inside
the browser, or from a prompt — the two are the same thing:

| | in `navigate` | at a prompt |
|---|---|---|
| **lead** — this window is the master | `F` | `navigate leader` (or `lead`) |
| **follow** — this window tracks the leader | `f` | `navigate follow` (or `follower`) |
| **neither** | `F` / `f` again | `navigate solo` |

`navigate status` says which role this window holds and who the leader is.
`navigate follow on|off|toggle|status` still works and means the same thing.

**Following needs somebody to follow.** With no leader anywhere, `navigate
follow` prints

```
navigateur: no leader — run `n leader` in the window that should lead
```

and changes nothing, exiting 2 — rather than leaving you a follower that will
never move. `f` in the browser says the same in its status line. Set a leader
first; the order is `leader` in one window, then `follow` in the others. A
window that is *itself* the leader gets the same refusal if it types `follow`:
it does not count as its own leader, so the refusal cannot leave you with nobody
leading.

There is **exactly one leader**. Pressing `F` in a second window takes the lead
away from the first, which notices at its next prompt and goes solo — you never
have to demote anyone by hand.

The role belongs to the *window*, not to a browsing session: it survives
quitting the browser, and lasts until you change it or close the terminal.

### What the followers track

The leader's **current directory**, however it got there:

- you type `cd somewhere` → the followers move
- you press `↵` in the browser → the followers move there with you
- you *browse* in the leader → the followers move **live**, as you move

That last one is a preview. While you browse, "where the leader is" means the
highlighted row's **nearest enclosing directory** — the row itself if it is a
folder, its parent if it is a file. So arrowing between two files in the same
folder moves nobody; stepping into a new folder does. The same rule decides
where `↵`, `w` and `t` put you.

**Quitting the browser with `q` snaps the followers back** to the leader's real
directory — the preview is over, and that is where the leader window actually
is. Press `↵` instead and the leader goes there too, so everybody stays
together. (Earlier versions left the followers behind; they now follow the
window, not the browsing session.)

A follower that is *itself* running `navigate` does not just move its shell —
**its tree moves**, expanding out to the leader's directory while you watch. Put
two windows side by side and browse in the leader to see it.

### The two delivery modes

`navigate follow` reports which one that terminal got:

- **live** — the terminal jumps immediately, even sitting idle at a prompt.
  Uses zsh's `zle -F` on a per-terminal FIFO. An idle zsh is blocked reading
  its keyboard and this is the only way to wake it from outside.
- **lazy** — it catches up the next time you press Enter. Automatic fallback
  when `zle` isn't available; costs immediacy and nothing else.

Following is **per machine**. The shared state is a directory on disk, so two
ssh sessions into the same Fedora box follow each other, but a terminal on your
Mac can never lead one on that box — different filesystems, no shared seam.

Which one you get in **Warp specifically is unverified** — `zle -F` needs a live
interactive prompt, so it can't be tested from a script. Run `navigate follow` in a
Warp tab and it will tell you. If it says `lazy`, following still works, it just
waits for your next Enter.

The leader publishes without blocking, using `sysopen -o nonblock` from zsh's
`zsh/system` module. If that module is missing, the leader still writes the
shared file below and every follower degrades to lazy — nothing breaks.

`navigate status` also prints the *live* subscriber count (it counts FIFOs, and
a lazy terminal has none — `0 live-subscribed` while someone is following is
normal, not a fault).

### Three things that surprise people

The last broadcast directory is also kept in `~/.navigateur/cwd`, and *every*
follower — live ones included — checks it at each prompt. A window that starts
following after the leader has been browsing will therefore jump to that stored
directory on its next Enter, without any new broadcast.

Terminals closed without `navigate solo` clean themselves up: writing to their
FIFO returns `ENXIO`, which is exactly the "nobody is reading" signal, so the
sweep costs no extra work.

A window that is *killed* outright can leave its role behind in
`~/.navigateur/roles/`. Terminal names get recycled, so a fresh window landing
on that name would inherit a stranger's role — it doesn't: a role that the
current shell never took itself is dropped the first time you use `navigate`.

## Colours

`~/.navigateur/config.toml`, written on first run:

```toml
[colors]
accent      = "#d97757"   # the ❯ caret and the active root
dir         = "#7aa2f7"
file        = "#c0caf5"
dim         = "#565f89"
border      = "#3b4261"
selected_bg = "#292e42"

[behavior]
show_hidden    = false
follow_default = false   # start this window as the leader
```

`follow_default = true` is the **leader** side: it starts every browser session
as if you had pressed `F`, so that window takes the lead. It does not make any
terminal follow — that is still `f` / `navigate follow`, per window. A role you
have already set wins over it: the setting only speaks when the window has no
role yet.

Plain 24-bit hex, no palette slots. A bad value falls back key by key rather
than failing — a typo shouldn't cost you the browser you'd use to fix it.

## Files

| path | |
|---|---|
| `install.sh` | writes the `source` line into your `.zshrc`; `--uninstall` removes it |
| `src/nav.py` | the TUI — raw ANSI, never writes data to stdout |
| `src/nav.zsh` | the `nav()` function (`navigate`/`n` alias it), follow subscription, hooks |
| `~/.navigateur/` | `config.toml`, `cwd`, `roles/<tty>`, `sub/<tty>.fifo` |

Hooks install via `add-zsh-hook`, never by assigning `precmd_functions` — Warp
already has entries there and clobbering the array breaks the terminal.

## Not in this first pass

Fuzzy search, file operations (rename/delete/move), bookmarks, git-status
decorations, a bash integration, and following across machines.
