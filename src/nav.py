#!/usr/bin/env python3
"""
navigateur -- an expanding-tree file explorer that lives in the terminal,
with live multi-terminal directory following.

Stdlib only. Raw ANSI rather than curses, so colours stay plain hex in
config.toml instead of becoming indexed palette slots.

This process can never change its parent shell's directory, so it doesn't try:
it writes the chosen directory to the file named by $NAV_LASTDIR and the nav()
shell function cds there afterwards. stdout is the display, never a channel.
"""

from __future__ import annotations

import errno
import os
import select
import shutil
import signal
import subprocess
import sys
import shlex
import termios
import tty
from pathlib import Path
from urllib.parse import quote

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.11+ has it
    tomllib = None

STATE = Path(os.environ.get("NAV_STATE", str(Path.home() / ".navigateur")))
SUB = STATE / "sub"
ROLES = STATE / "roles"
CWD_FILE = STATE / "cwd"
CONFIG = STATE / "config.toml"

MAX_DEPTH = 40

DEFAULT_CONFIG_TEXT = '''\
# navigateur configuration. Colours are plain hex; edit and relaunch.

[colors]
accent      = "#d97757"   # the caret, the active root
dir         = "#7aa2f7"
file        = "#c0caf5"
dim         = "#565f89"   # hints, counts, disclosure triangles
border      = "#3b4261"
selected_bg = "#292e42"

[behavior]
show_hidden    = false
follow_default = false   # start this window as the leader
'''

DEFAULTS = {
    "colors": {
        "accent": "#d97757",
        "dir": "#7aa2f7",
        "file": "#c0caf5",
        "dim": "#565f89",
        "border": "#3b4261",
        "selected_bg": "#292e42",
    },
    "behavior": {"show_hidden": False, "follow_default": False},
}


# --------------------------------------------------------------------------- config


def load_config() -> dict:
    """Merge config.toml over the defaults. A bad value must never cost you
    the file browser, so every lookup falls back key by key."""
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        if not CONFIG.exists():
            CONFIG.write_text(DEFAULT_CONFIG_TEXT)
    except OSError:
        return cfg
    if tomllib is None:
        return cfg
    try:
        with CONFIG.open("rb") as fh:
            user = tomllib.load(fh)
    except (OSError, ValueError):
        return cfg
    for section, values in cfg.items():
        got = user.get(section)
        if isinstance(got, dict):
            for key in values:
                if key in got and isinstance(got[key], type(values[key])):
                    values[key] = got[key]
    return cfg


def fg(hexcolor: str, fallback: str = "#c0caf5") -> str:
    r, g, b = _rgb(hexcolor, fallback)
    return f"\x1b[38;2;{r};{g};{b}m"


def bg(hexcolor: str, fallback: str = "#292e42") -> str:
    r, g, b = _rgb(hexcolor, fallback)
    return f"\x1b[48;2;{r};{g};{b}m"


def _rgb(value: str, fallback: str) -> tuple[int, int, int]:
    for candidate in (value, fallback):
        s = str(candidate).lstrip("#")
        if len(s) == 6:
            try:
                return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            except ValueError:
                pass
    return (192, 202, 245)


RESET = "\x1b[0m"
BOLD = "\x1b[1m"


# ----------------------------------------------------------------------------- fs


def list_dir(d: Path, show_hidden: bool) -> list[tuple[str, Path, bool]]:
    try:
        entries = list(os.scandir(d))
    except OSError:
        return []
    out = []
    for e in entries:
        if not show_hidden and e.name.startswith("."):
            continue
        try:
            is_dir = e.is_dir()
        except OSError:
            is_dir = False
        out.append((e.name, Path(e.path), is_dir))
    out.sort(key=lambda t: (not t[2], t[0].lower()))
    return out


def home_short(p: Path) -> str:
    s = str(p)
    home = str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


# ------------------------------------------------------------------------- follow


def self_tty() -> str | None:
    """Basename of this terminal's tty. MUST match zsh's ${TTY:t} exactly or
    self-exclusion silently fails and the driving terminal fights itself."""
    for fd in (0, 1, 2):
        try:
            return os.path.basename(os.ttyname(fd))
        except OSError:
            continue
    return None


def read_role(me: str | None) -> str:
    """This window's role: leader, follower or solo.

    $NAV_STATE/roles/<tty> is the single source of truth and the seam with the
    shell. Writing it is all this process can do about a role -- only a shell
    can register a FIFO with `zle -F` -- so nav.zsh's _nav_reconcile brings the
    live machinery in line afterwards. An unreadable or junk value degrades to
    solo rather than costing you the browser."""
    if me is None:
        return "solo"
    try:
        value = (ROLES / me).read_text().strip()
    except OSError:
        return "solo"
    return value if value in ("leader", "follower") else "solo"


def write_role(me: str | None, role: str) -> bool:
    if me is None:
        return False
    try:
        ROLES.mkdir(parents=True, exist_ok=True)
        if role == "solo":
            (ROLES / me).unlink(missing_ok=True)
        else:
            (ROLES / me).write_text(role + "\n")
    except OSError:
        return False
    if role == "leader":
        # Exactly one leader. Enforced only here, on promotion -- reading a
        # role never globs this directory, it reads one file by name.
        try:
            others = [q for q in ROLES.iterdir() if q.name != me]
        except OSError:
            others = []
        for q in others:
            try:
                if q.read_text().strip() == "leader":
                    q.unlink()
            except OSError:
                pass
    return True


def read_cwd() -> str | None:
    """Where the leader is. Both publishers -- publish() below and nav.zsh's
    _nav_publish -- write this file, so it is the one place a follower has to
    look."""
    try:
        return CWD_FILE.read_text().strip() or None
    except OSError:
        return None


def publish(path: Path, me: str | None) -> None:
    """Broadcast a directory to every subscribed terminal."""
    payload = (str(path) + "\n").encode()
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        CWD_FILE.write_text(str(path) + "\n")
    except OSError:
        pass
    try:
        fifos = sorted(SUB.glob("*.fifo"))
    except OSError:
        return
    for f in fifos:
        if me is not None and f.stem == me:
            continue  # the driving terminal cds on exit; don't fight it
        try:
            fd = os.open(str(f), os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno == errno.ENXIO:
                # No reader: that terminal died without cleanup. Garbage
                # collection rides along on the write we were doing anyway.
                try:
                    f.unlink()
                except OSError:
                    pass
            continue
        try:
            os.write(fd, payload)  # one newline-terminated write: a partial
        except OSError:            # line would block `read -r` inside zle -F
            pass                   # and freeze that terminal's line editor.
        finally:
            os.close(fd)


# ------------------------------------------------------------------------ screen


class Screen:
    def __init__(self) -> None:
        self.fd = sys.stdin.fileno()
        self.saved = None
        self.prev: list[str] = []
        self.rows = 24
        self.cols = 80

    def __enter__(self) -> "Screen":
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, self._bail)
        signal.signal(signal.SIGWINCH, self._resized)
        return self

    def __exit__(self, *exc) -> None:
        self.restore()

    def restore(self) -> None:
        sys.stdout.write("\x1b[?25h\x1b[?1049l" + RESET)
        sys.stdout.flush()
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            self.saved = None

    def _bail(self, *_a) -> None:
        self.restore()
        os._exit(0)

    def _resized(self, *_a) -> None:
        self.prev = []  # force a full repaint on the next frame

    def measure(self) -> tuple[int, int]:
        """shutil's version honours $COLUMNS/$LINES and has a fallback. Clamp
        anyway: a terminal that reports 0x0 (or mid-resize garbage) would
        otherwise render an empty screen."""
        size = shutil.get_terminal_size(fallback=(80, 24))
        cols, rows = max(40, size.columns), max(8, size.lines)
        if (cols, rows) != (self.cols, self.rows):
            self.prev = []
        self.cols, self.rows = cols, rows
        return cols, rows

    def paint(self, lines: list[str]) -> None:
        """Repaint only the lines that changed. Full clears flicker in Warp."""
        out = []
        if not self.prev:
            out.append("\x1b[2J")
        for i, line in enumerate(lines):
            if i < len(self.prev) and self.prev[i] == line:
                continue
            out.append(f"\x1b[{i + 1};1H\x1b[K{line}")
        for i in range(len(lines), len(self.prev)):
            out.append(f"\x1b[{i + 1};1H\x1b[K")
        if out:
            sys.stdout.write("".join(out) + RESET)
            sys.stdout.flush()
        self.prev = list(lines)

    def key(self, timeout: float | None = None) -> str | None:
        """timeout is how a follower stays responsive to the leader while
        nobody is typing: select first, and report None when it expires."""
        if timeout is not None:
            r, _, _ = select.select([self.fd], [], [], timeout)
            if not r:
                return None
        try:
            ch = os.read(self.fd, 1)
        except (OSError, InterruptedError):
            return None
        if not ch:
            return None
        if ch == b"\x1b":
            r, _, _ = select.select([self.fd], [], [], 0.03)
            if not r:
                return "ESC"
            try:
                seq = os.read(self.fd, 2)
            except OSError:
                return "ESC"
            return {
                b"[A": "UP", b"[B": "DOWN", b"[C": "RIGHT", b"[D": "LEFT",
                b"OA": "UP", b"OB": "DOWN", b"OC": "RIGHT", b"OD": "LEFT",
            }.get(seq, "ESC")
        if ch in (b"\r", b"\n"):
            return "ENTER"
        try:
            return ch.decode("utf-8")
        except UnicodeDecodeError:
            return None


# -------------------------------------------------------------------------- app


class Row:
    __slots__ = ("path", "depth", "is_dir")

    def __init__(self, path: Path, depth: int, is_dir: bool) -> None:
        self.path, self.depth, self.is_dir = path, depth, is_dir


class Navigateur:
    def __init__(self, root: Path, cfg: dict) -> None:
        self.root = root
        self.cfg = cfg
        self.colors = cfg["colors"]
        self.show_hidden = bool(cfg["behavior"]["show_hidden"])
        self.expanded: set[str] = set()
        self.cursor = 0
        self.top = 0
        self.rows: list[Row] = []
        self.me = self_tty()
        self.published: str | None = None
        self.role = read_role(self.me)
        self.seen = read_cwd()  # what a follower has already acted on
        if self.role == "solo" and bool(cfg["behavior"]["follow_default"]):
            # The old broadcast-by-default switch, read as "start this window
            # leading". A saved role always wins: the config only gets a say
            # when there is no role file at all.
            write_role(self.me, "leader")
            self.role = "leader"
        self.chosen: Path | None = None
        self.message = ""
        self.rebuild()

    # -- model ------------------------------------------------------------

    def rebuild(self) -> None:
        rows: list[Row] = []

        def walk(d: Path, depth: int) -> None:
            if depth > MAX_DEPTH:
                return
            for _name, path, is_dir in list_dir(d, self.show_hidden):
                rows.append(Row(path, depth, is_dir))
                if is_dir and str(path) in self.expanded:
                    walk(path, depth + 1)

        walk(self.root, 0)
        self.rows = rows
        self.cursor = max(0, min(self.cursor, len(rows) - 1))

    def current(self) -> Row | None:
        return self.rows[self.cursor] if self.rows else None

    def published_dir(self) -> Path:
        """THE publish rule: the nearest enclosing directory of the highlighted
        row -- the row itself when it is a directory, its parent when it is a
        file. Everything (followers, $NAV_LASTDIR) reads this one function."""
        row = self.current()
        if row is None:
            return self.root
        return row.path if row.is_dir else row.path.parent

    def maybe_publish(self) -> None:
        if self.role != "leader":
            return  # structural: a follower can never publish
        target = str(self.published_dir())
        if target == self.published:
            return  # de-dupe: arrowing between sibling files must not re-fire
        self.published = target
        publish(Path(target), self.me)

    def select_path(self, path: Path) -> None:
        for i, row in enumerate(self.rows):
            if row.path == path:
                self.cursor = i
                return

    def set_role(self, role: str) -> bool:
        if not write_role(self.me, role):
            self.message = "cannot write the role file"
            return False
        self.role = role
        self.published = None  # a fresh leader must state where it is
        return True

    def sync_from_leader(self, force: bool = False) -> bool:
        """Follower poll. Reads $NAV_STATE/cwd rather than this terminal's
        FIFO: the shell holds that FIFO open for `zle -F` and a second reader
        would race it for the bytes. The shell keeps its own copy of the
        message, so quitting still lands the shell in the same place."""
        dest = read_cwd()
        if dest is None or (dest == self.seen and not force):
            return False
        self.seen = dest  # recorded before acting, like _NAV_SEEN in nav.zsh
        target = Path(dest)
        if not target.is_dir() or target == self.published_dir():
            return False
        self.reveal_path(target)
        return True

    def reveal_path(self, target: Path) -> None:
        """Put the cursor on target: expand the ancestors when it is under the
        current root, otherwise re-root there -- the same move `h` makes when
        it runs out of parents."""
        try:
            rel = target.relative_to(self.root)
        except ValueError:
            self.root = target
            self.expanded.add(str(target))
            self.rebuild()
            self.cursor = 0
            return
        if not rel.parts:
            self.rebuild()
            self.cursor = 0
            return
        node = self.root
        for part in rel.parts:
            node = node / part
            self.expanded.add(str(node))
        self.rebuild()
        self.select_path(target)

    # -- actions ----------------------------------------------------------

    def move(self, delta: int) -> None:
        if self.rows:
            self.cursor = max(0, min(self.cursor + delta, len(self.rows) - 1))

    def enter(self) -> None:
        """l -- expand a folder. On a file, expand nothing; the publish rule
        already puts followers in that file's directory."""
        row = self.current()
        if row is None:
            return
        if row.is_dir:
            self.expanded.add(str(row.path))
            self.rebuild()

    def leave(self) -> None:
        """h -- collapse if expanded, else jump to the parent row, else re-root
        one level up. That last case is the `..` from start.txt."""
        row = self.current()
        if row is not None and row.is_dir and str(row.path) in self.expanded:
            self.expanded.discard(str(row.path))
            self.rebuild()
            return
        if row is not None and row.depth > 0:
            parent = row.path.parent
            self.select_path(parent)
            return
        parent = self.root.parent
        if parent == self.root:
            return  # already at /
        old = self.root
        self.root = parent
        self.expanded.add(str(old))
        self.rebuild()
        self.select_path(old)

    def open_it(self, reveal: bool = False) -> None:
        row = self.current()
        if row is None:
            return
        args = ["open", "-R", str(row.path)] if reveal else ["open", str(row.path)]
        try:
            subprocess.run(args, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
            self.message = ("revealed " if reveal else "opened ") + row.path.name
        except OSError as exc:
            self.message = f"open failed: {exc}"

    def spawn_terminal(self, new_window: bool) -> None:
        """w / t -- open a new terminal window or tab already cd'd here.

        published_dir(), deliberately, and not row.path the way open_it() does:
        `o` opens the highlighted *file*, but a shell can only cd to a
        directory, so this is the same "where am I" rule the followers and
        $NAV_LASTDIR use. macOS only, like open_it().

        Every unsupported case sets a message and returns. This key must never
        be the one that raises out of the browser."""
        d = str(self.published_dir())
        term = os.environ.get("TERM_PROGRAM", "")
        what = "window" if new_window else "tab"
        # cd argument for the AppleScript terminals: shell-quoted, then escaped
        # again for the AppleScript string literal it gets embedded in.
        cd = "cd " + shlex.quote(d).replace("\\", "\\\\").replace('"', '\\"')

        if term == "WarpTerminal":
            action = "new_window" if new_window else "new_tab"
            args = ["open", f"warp://action/{action}?path={quote(d)}"]
        elif term == "iTerm.app":
            create = ("set w to (create window with default profile)"
                      if new_window else
                      "if (count of windows) is 0 then\n"
                      "  set w to (create window with default profile)\n"
                      "else\n"
                      "  set w to current window\n"
                      "  tell w to create tab with default profile\n"
                      "end if")
            args = ["osascript", "-e",
                    f'tell application "iTerm"\n'
                    f'{create}\n'
                    f'tell current session of w to write text "{cd}"\n'
                    f'activate\n'
                    f'end tell']
        elif term == "Apple_Terminal":
            if not new_window:
                # `do script` only ever makes a window; a tab needs a synthetic
                # cmd-t through System Events, i.e. an Accessibility grant.
                self.message = "Terminal.app has no scriptable new tab -- w works"
                return
            args = ["osascript", "-e",
                    f'tell application "Terminal"\n'
                    f'do script "{cd}"\n'
                    f'activate\n'
                    f'end tell']
        else:
            self.message = (f"no new-{what} support for "
                            f"{term or 'this terminal (TERM_PROGRAM unset)'}")
            return

        try:
            subprocess.run(args, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
            self.message = f"new {what} in {home_short(Path(d))}"
        except OSError as exc:
            self.message = f"new {what} failed: {exc}"

    # -- render -----------------------------------------------------------

    def frame(self, cols: int, height: int) -> list[str]:
        c = self.colors
        border, dim, accent = fg(c["border"]), fg(c["dim"]), fg(c["accent"])
        width = max(30, min(cols, 200))
        inner = width - 2
        body_h = max(3, height - 4)

        # keep the cursor inside the viewport, with a little breathing room
        if self.cursor < self.top + 2:
            self.top = max(0, self.cursor - 2)
        if self.cursor > self.top + body_h - 3:
            self.top = self.cursor - body_h + 3
        self.top = max(0, min(self.top, max(0, len(self.rows) - body_h)))

        if self.role == "leader":
            flag, flag_fg = " leading ", accent
        elif self.role == "follower":
            flag, flag_fg = " following ", dim
        else:
            flag, flag_fg = "", ""
        # The title must never widen the box: elide from the LEFT, since the
        # tail of a path is the informative half.
        room = inner - 3 - len(flag)
        raw = home_short(self.root)
        if len(raw) > room:
            raw = "…" + raw[-(room - 1):] if room > 1 else ""
        title = " " + raw + " "
        fill = max(0, inner - 1 - len(title) - len(flag) - 1)
        lines = [
            f"{border}╭─{accent}{BOLD}{title}{RESET}"
            f"{flag_fg}{flag}{border}{'─' * fill}╮{RESET}"
        ]

        for i in range(body_h):
            idx = self.top + i
            if idx >= len(self.rows):
                lines.append(f"{border}│{RESET}{' ' * inner}{border}│{RESET}")
                continue
            lines.append(self.render_row(idx, inner, border))

        lines.append(f"{border}╰{'─' * inner}╯{RESET}")

        # Drop whole hint segments rather than slicing a word in half.
        if self.message:
            hint = self.message
        else:
            segs = ["hjkl move", "o open", "O reveal", "F lead", "f follow",
                    ". hidden", "↵ cd here", "w window", "t tab", "q quit"]
            hint = " · ".join(segs)
            while segs and len(hint) > cols - 3:
                segs.pop(-2 if len(segs) > 1 else 0)
                hint = " · ".join(segs)
        lines.append(f"  {dim}{hint[:max(0, cols - 3)]}{RESET}")
        while len(lines) < height:
            lines.append("")
        return lines[:height]

    def render_row(self, idx: int, inner: int, border: str) -> str:
        c = self.colors
        row = self.rows[idx]
        selected = idx == self.cursor
        caret = f"{fg(c['accent'])}❯{RESET}" if selected else " "
        # clamp the indent so very deep nesting can't drive text_w negative
        pad = "  " * min(row.depth, max(0, (inner - 12) // 2))
        if row.is_dir:
            tri = "▾" if str(row.path) in self.expanded else "▸"
            name = f"{fg(c['dim'])}{tri} {fg(c['dir'])}{row.path.name}/{RESET}"
            plain = f"{tri} {row.path.name}/"
        else:
            name = f"  {fg(c['file'])}{row.path.name}{RESET}"
            plain = f"  {row.path.name}"

        text_w = inner - 3 - len(pad)
        if len(plain) > text_w > 0:
            keep = text_w - 1
            trimmed = plain[:keep] + "…"
            colour = fg(c["dir"]) if row.is_dir else fg(c["file"])
            name = f"{colour}{trimmed}{RESET}"
            plain = trimmed

        gap = max(0, inner - 3 - len(pad) - len(plain))
        body = f" {caret} {pad}{name}{' ' * gap}"
        if selected:
            body = f"{bg(c['selected_bg'])}{body}{RESET}"
        return f"{border}│{RESET}{body}{border}│{RESET}"

    # -- loop -------------------------------------------------------------

    def run(self, screen: Screen) -> None:
        self.maybe_publish()
        while True:
            cols, height = screen.measure()
            screen.paint(self.frame(cols, height))
            # A follower wakes up on its own to check on the leader;
            # everybody else blocks on the keyboard the way they always did.
            key = screen.key(0.2 if self.role == "follower" else None)
            if key is None:
                # Poll *before* looping, or this is a 5Hz spin on measure()'s
                # ioctl with nothing to show for it.
                if self.role == "follower":
                    self.sync_from_leader()
                continue
            had_message = bool(self.message)
            self.message = ""

            if key in ("q", "ESC"):
                return
            elif key == "ENTER":
                self.chosen = self.published_dir()
                return
            elif key in ("j", "DOWN"):
                self.move(1)
            elif key in ("k", "UP"):
                self.move(-1)
            elif key in ("l", "RIGHT"):
                self.enter()
            elif key in ("h", "LEFT"):
                self.leave()
            elif key == "g":
                self.cursor = 0
            elif key == "G":
                self.cursor = max(0, len(self.rows) - 1)
            elif key == "o":
                self.open_it()
            elif key == "O":
                self.open_it(reveal=True)
            elif key in ("w", "t"):
                self.spawn_terminal(new_window=(key == "w"))
                # A new tab lands on top of this session and no SIGWINCH fires,
                # so paint()'s diff would keep a stale frame. Blank prev the way
                # _resized() does and repaint whole.
                screen.prev = []
            elif key == "F":
                if self.role == "leader":
                    if self.set_role("solo"):
                        self.message = "stopped leading"
                elif self.set_role("leader"):
                    self.message = "leading — followers track this window"
            elif key == "f":
                if self.role == "follower":
                    if self.set_role("solo"):
                        self.message = "stopped following"
                elif self.set_role("follower"):
                    self.message = "following the leader"
                    self.sync_from_leader(force=True)  # `f` means "sync me now"
            elif key == ".":
                self.show_hidden = not self.show_hidden
                self.rebuild()
            elif key in ("\x04", "\x03"):  # ctrl-d / ctrl-c
                return
            elif not had_message:
                continue
            self.maybe_publish()


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("-")]
    start = Path(args[0]).expanduser() if args else Path.cwd()
    try:
        start = start.resolve()
    except OSError:
        start = Path.cwd()
    if not start.is_dir():
        start = start.parent

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write("navigateur: needs a terminal\n")
        return 2

    cfg = load_config()
    app = Navigateur(start, cfg)
    with Screen() as screen:
        try:
            app.run(screen)
        except KeyboardInterrupt:
            pass

    if app.chosen is not None:
        target = os.environ.get("NAV_LASTDIR")
        if target:
            try:
                Path(target).write_text(str(app.chosen) + "\n")
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
