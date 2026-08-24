# navigateur

A file explorer that lives in the terminal. Expanding tree like the VSCode
explorer, `hjkl` keys, hex-configurable colours — and it can make your *other*
terminals follow along as you browse.

```
╭─ ~/Desktop/navigateur  following ───────╮
│   ▾ src/                                │
│ ❯     nav.py                            │
│       nav.zsh                           │
│     README.md                           │
│     start.txt                           │
╰─────────────────────────────────────────╯
  hjkl move · o open · O reveal · q quit
```

Stdlib-only Python 3 + zsh. No dependencies, no build step.

## Install

```sh
echo 'source ~/Desktop/navigateur/src/nav.zsh' >> ~/.zshrc
exec zsh
```

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
| `f` | start/stop broadcasting — while on, every move drags the following terminals along |
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

Unsupported combinations say so on the hint line and do nothing else. Like `o`
and `O`, this is macOS-only.

The two Warp deep links are verified against Warp 0.2026.08.19 rather than
assumed: they log `root_view:add_session_at_path` and
`root_view:open_new_from_path` in `~/Library/Logs/warp.log`, where an action
Warp doesn't know logs `Received "action" intent with unexpected action`
instead. **Warp has no pane action** — `split_pane`, `new_pane`, `add_pane` and
`split_pane_right` are all rejected that way, so `t` cannot split the current
tab through the URI scheme.

## Making other terminals follow

Following has **two halves, and you need both**. They are set independently:

| | where | how | scope |
|---|---|---|---|
| **subscribe** — this terminal accepts being moved | any terminal | `navigate follow on` | that terminal, until `off` or it closes |
| **broadcast** — this browser sends where it is | inside the browser | press `f` | that browsing session |

So: run `navigate follow on` in the terminals you want dragged around, open
`navigate` in another one, and press **`f`**.

**`f` is a mode, not a send.** It does not push the current directory once — it
switches broadcasting on and leaves it on. From then on every `j`/`k`/`l`/`h`
that changes directory moves the subscribed terminals immediately, live, as you
browse. Press `f` again to stop; they stay wherever they last landed.

You can always see which state you are in: the title bar shows a dim
`following` while broadcasting is on, and toggling prints
`broadcasting to following terminals` / `stopped broadcasting` on the hint line.

**What they cd to** is defined by one rule: *the nearest enclosing directory of
the highlighted row* — the row itself if it's a folder, its parent if it's a
file. So arrowing between two files in the same folder does not move anybody;
stepping into a new folder does. The same rule decides where `↵` puts you.

Quitting does not pull anyone back: `q` and `↵` only affect *your* shell, and
the subscribed terminals keep the last directory you broadcast.

### The two delivery modes

`navigate follow on` reports which one that terminal got:

- **live** — the terminal jumps immediately, even sitting idle at a prompt.
  Uses zsh's `zle -F` on a per-terminal FIFO. An idle zsh is blocked reading
  its keyboard and this is the only way to wake it from outside.
- **lazy** — it catches up the next time you press Enter. Automatic fallback
  when `zle` isn't available; costs immediacy and nothing else.

Which one you get in **Warp specifically is unverified** — `zle -F` needs a live
interactive prompt, so it can't be tested from a script. Run `navigate follow on` in a
Warp tab and it will tell you. If it says `lazy`, following still works, it just
waits for your next Enter.

`navigate follow status` tells you the mode and the *live* subscriber count
(it counts FIFOs, and a lazy terminal has none — `0 subscribed` while following is
normal, not a fault);
`navigate follow toggle` flips subscription for the current terminal.

### Two things that surprise people

The last broadcast directory is also kept in `~/.navigateur/cwd`, and *every*
subscribed terminal — live ones included — checks it at each prompt. A terminal
that subscribes after you've already been browsing will therefore jump to that
stored directory on its next Enter, without any new broadcast.

Terminals closed without `navigate follow off` clean themselves up: writing to their
FIFO returns `ENXIO`, which is exactly the "nobody is reading" signal, so the
sweep costs no extra work.

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
follow_default = false
```

`follow_default = true` is the **broadcast** side, not the subscribe side: it
starts every browser session as if you had already pressed `f`. It does not make
any terminal follow — that is still `navigate follow on`, per terminal.

Plain 24-bit hex, no palette slots. A bad value falls back key by key rather
than failing — a typo shouldn't cost you the browser you'd use to fix it.

## Files

| path | |
|---|---|
| `src/nav.py` | the TUI — raw ANSI, never writes data to stdout |
| `src/nav.zsh` | the `nav()` function (`navigate`/`n` alias it), follow subscription, hooks |
| `~/.navigateur/` | `config.toml`, `cwd`, `sub/<tty>.fifo` |

Hooks install via `add-zsh-hook`, never by assigning `precmd_functions` — Warp
already has entries there and clobbering the array breaks the terminal.

## Not in this first pass

Fuzzy search, file operations (rename/delete/move), bookmarks, git-status
decorations, and non-macOS `open` equivalents.
