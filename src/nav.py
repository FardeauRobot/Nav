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
import time
import tty
from pathlib import Path
from urllib.parse import quote

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.11+ has it
    tomllib = None

# Without tomllib the settings panel can still *write* config.toml, but
# load_config() will never read it back -- so a rebind or a recolour is real for
# this session and silently gone at the next launch. Say so rather than
# reporting a success the user will not get: the same "degrade honestly" rule
# load_config() follows key by key.
SESSION_ONLY = "" if tomllib else "  (this session only -- python < 3.11)"

STATE = Path(os.environ.get("NAV_STATE", str(Path.home() / ".navigateur")))
ROLES = STATE / "roles"
GROUPS = STATE / "groups"
CONFIG = STATE / "config.toml"
DEFAULT_GROUP = "default"

# A group name becomes a path component, so `n lead ../../etc` has to be refused
# before anything builds a path out of it. nav.zsh's _nav_group_ok must accept
# exactly this set -- the same class of rule as the self_tty() symmetry one.
_GROUP_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def group_ok(group: str) -> bool:
    return 1 <= len(group) <= 32 and set(group) <= _GROUP_CHARS


def name_ok(name: str) -> str:
    """Why `name` cannot be a new file's name, or "" if it can. A filename is
    one path component, never a `/`-separated path that could escape the
    browsed directory via `..` -- the same class of rule as group_ok()."""
    if "/" in name:
        return "name cannot contain /"
    if name in (".", ".."):
        return f"'{name}' is not a valid name"
    return ""


# One group's broadcast directory. The group is in the *path* here and in the
# *content* of roles/<tty>, and the split is forced by which paths are hot:
# read_role() reads one file by a name it already knows and runs at every prompt
# through nav.zsh's _nav_reconcile, while publish() globs its subscribers on
# every cursor move and must not decide membership by reading role files.
def sub_dir(group: str) -> Path:
    return GROUPS / group / "sub"


def cwd_file(group: str) -> Path:
    return GROUPS / group / "cwd"


def gen_file(group: str) -> Path:
    return GROUPS / group / "gen"


BOOKMARKS = STATE / "bookmarks"


def bookmark_file(slot: str) -> Path:
    return BOOKMARKS / slot


def read_bookmark(slot: str) -> Path | None:
    try:
        text = bookmark_file(slot).read_text().strip()
    except OSError:
        return None
    return Path(text) if text else None


def write_bookmark(slot: str, path: Path) -> bool:
    try:
        BOOKMARKS.mkdir(parents=True, exist_ok=True)
        bookmark_file(slot).write_text(str(path) + "\n")
    except OSError:
        return False
    return True


MAX_DEPTH = 40

DEFAULT_CONFIG_TEXT = '''\
# navigateur configuration. Colours are plain hex. Edit this file, or press `,`
# in the browser -- it patches the one line it changes and leaves the rest be.

[colors]
accent      = "#fe8019"   # the caret, the active root
dir         = "#83a598"
file        = "#ebdbb2"
dim         = "#928374"   # hints, counts, disclosure triangles
border      = "#504945"
selected_bg = "#3c3836"

[behavior]
show_hidden    = false
follow_default = false   # start this window as the leader
'''

DEFAULTS = {
    "colors": {
        "accent": "#fe8019",
        "dir": "#83a598",
        "file": "#ebdbb2",
        "dim": "#928374",
        "border": "#504945",
        "selected_bg": "#3c3836",
    },
    "behavior": {"show_hidden": False, "follow_default": False},
}


# ------------------------------------------------------------------------- keymap

# THE key table. It is the single source of truth for two things at once: what
# the `?` panel prints, and what run() actually dispatches on. An action with no
# row here is unreachable; a row whose action run() never tests is a lie printed
# on screen. Keep them in step -- _selftest() below asserts it.
#
# A row is (action | None, key, alias, description). `action` is None for the
# *fixed* keys: ENTER, ESC, q and the ctrl pair are shown in the table but can
# never be rebound. That is the same reasoning as load_config()'s key-by-key
# fallback -- a binding typo must never cost you the browser you would use to
# fix it -- so the way out is never something the user can spell wrong.
# `alias` is the arrow equivalent, so the table can print "j ↓" without needing
# a second table to look arrows up in.
KEY_SECTIONS: list[tuple[str, list[tuple[str | None, str, str, str]]]] = [
    ("moving", [
        ("down", "j", "↓", "down a row"),
        ("up", "k", "↑", "up a row"),
        ("enter", "l", "→", "expand a folder"),
        ("leave", "h", "←", "collapse, or go up"),
        ("top", "g", "", "first row"),
        ("bottom", "G", "", "last row"),
        (None, "↵", "", "cd here and quit"),
    ]),
    ("opening", [
        ("open", "o", "", "open in the default app"),
        ("reveal", "O", "", "reveal in the file manager"),
        ("edit", "E", "", "edit in neovim"),
        ("window", "w", "", "new terminal window here"),
        ("tab", "t", "", "new terminal tab here"),
        ("hidden", ".", "", "show or hide dotfiles"),
    ]),
    ("files", [
        ("new_file", "n", "", "new empty file here"),
        ("mark", "e", "", "mark or unmark a row"),
        ("op_move", "m", "", "move marked items here"),
        ("op_copy", "c", "", "copy marked items here"),
        ("op_cut", "x", "", "cut marked items here"),
        ("op_delete", "d", "", "delete marked items"),
    ]),
    ("bookmarks", [
        ("bookmark_set", "B", "", "bookmark here, then a digit"),
        ("bookmark_go", "b", "", "jump to a bookmark digit"),
    ]),
    ("following", [
        ("lead", "F", "", "lead this group, or stop"),
        ("follow", "f", "", "follow this group, or stop"),
    ]),
    ("panels", [
        ("panel_keys", "?", "", "this table"),
        ("panel_settings", ",", "", "colours, keys, bookmarks"),
    ]),
    ("leaving", [
        (None, "esc", "", "back out of a mode or a panel"),
        (None, "q", "", "quit -- always, from anywhere"),
        (None, "^c", "^d", "quit"),
    ]),
]

# Every remappable action, in table order. Rows with action None are the fixed
# keys and deliberately never reach this dict.
DEFAULT_KEYS = {
    action: key
    for _section, rows in KEY_SECTIONS
    for action, key, _alias, _desc in rows
    if action is not None
}

# action -> its one-line description, so the settings panel can name an action
# without a second copy of the wording.
ACTION_DESC = {
    action: desc
    for _section, rows in KEY_SECTIONS
    for action, _key, _alias, desc in rows
    if action is not None
}

# Arrows are bound to the same actions as hjkl and are not part of the keymap:
# rebinding "down" to `n` must not cost you the down arrow.
ARROW_KEYS = {"DOWN": "down", "UP": "up", "RIGHT": "enter", "LEFT": "leave"}

# Keys no binding may claim. ENTER/ESC/q/^c/^d are the fixed rows above; the
# digits are excluded because B and b read one as a bookmark slot.
RESERVED_KEYS = frozenset({"q", "ESC", "ENTER", "\x03", "\x04"} | set("0123456789"))

# The keymap joins the config, so DEFAULTS and DEFAULT_CONFIG_TEXT grow a
# [keys] section -- generated from the table above rather than written out by
# hand, which is the only way the template can't drift from it.
DEFAULTS["keys"] = dict(DEFAULT_KEYS)
DEFAULT_CONFIG_TEXT += "\n[keys]\n# One key per action. Press `,` in the browser to change these.\n"
DEFAULT_CONFIG_TEXT += "".join(
    f'{action:<15}= "{key}"\n' for action, key in DEFAULT_KEYS.items())


def key_ok(key: str, action: str, keys: dict[str, str]) -> str:
    """Why `key` cannot be bound to `action`, or "" if it can. One function so
    the config loader and the settings panel refuse exactly the same set --
    the same class of rule as group_ok()."""
    if len(key) != 1 or not key.isprintable():
        return "needs to be one printable character"
    if key in RESERVED_KEYS:
        return f"{key} is reserved"
    for other, bound in keys.items():
        if bound == key and other != action:
            return f"{key} is already {ACTION_DESC[other]}"
    return ""


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
    # [keys] needs more than a type check: a binding can be reserved, or can
    # collide with another action, and either one would be resolved silently by
    # dispatch order. Same degrade-key-by-key rule -- the bad entry falls back
    # to its default, the rest of the file still applies.
    for action in DEFAULT_KEYS:
        if key_ok(cfg["keys"][action], action, cfg["keys"]):
            cfg["keys"][action] = DEFAULT_KEYS[action]
    return cfg


def toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_config_value(section: str, key: str, value: str) -> bool:
    """Patch one key in config.toml and leave every other byte alone.

    tomllib is read-only and there is no stdlib writer, but the fix is NOT to
    regenerate the file from DEFAULT_CONFIG_TEXT: that template is written only
    on first run, so by now the file holds the user's comments, their spacing,
    and possibly keys this version has never heard of. All of that has to
    survive being able to change one colour from the settings panel."""
    try:
        text = CONFIG.read_text() if CONFIG.exists() else DEFAULT_CONFIG_TEXT
    except OSError:
        return False
    lines = text.splitlines()
    quoted = toml_quote(value)

    here = ""          # the section we are inside right now
    end_of_section = None  # last line index still belonging to `section`
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            here = stripped[1:-1].strip()
            continue
        if here != section:
            continue
        end_of_section = i
        name, sep, rest = line.partition("=")
        if not sep or name.strip() != key:
            continue
        # Keep the indentation, the name column and any trailing comment;
        # replace the value and nothing else. The comment has to be found
        # *after* the value, not with a bare rest.index("#") -- every colour in
        # this file is a "#rrggbb" string and that would cut one in half.
        comment = _trailing_comment(rest)
        pad = "   " if comment else ""
        lines[i] = f"{name}= {quoted}{pad}{comment}".rstrip()
        return _save_config(lines)

    new = f"{key} = {quoted}"
    if end_of_section is None:
        lines += ["", f"[{section}]", new]
    else:
        lines.insert(end_of_section + 1, new)
    return _save_config(lines)


def _trailing_comment(rest: str) -> str:
    """The `# ...` after a TOML value, skipping over a quoted string so that a
    "#d97757" colour is never mistaken for the start of a comment."""
    after = rest.lstrip()
    if after.startswith('"'):
        i = 1
        while i < len(after):
            if after[i] == "\\":
                i += 2
                continue
            if after[i] == '"':
                i += 1
                break
            i += 1
        after = after[i:]
    return after[after.index("#"):] if "#" in after else ""


def _save_config(lines: list[str]) -> bool:
    """Write via a temp file in the same directory, then rename: a config
    truncated by a crash mid-write would take the colours with it."""
    tmp = CONFIG.with_suffix(".toml.tmp")
    try:
        tmp.write_text("\n".join(lines) + "\n")
        os.replace(tmp, CONFIG)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def fg(hexcolor: str, fallback: str = "#ebdbb2") -> str:
    r, g, b = _rgb(hexcolor, fallback)
    return f"\x1b[38;2;{r};{g};{b}m"


def bg(hexcolor: str, fallback: str = "#3c3836") -> str:
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


# ------------------------------------------------------------------- the desktop

IS_MAC = sys.platform == "darwin"

# Terminals that can be told where to start, and how. A `None` tab command means
# that terminal has no scriptable new tab -- the Terminal.app case, on Linux.
LINUX_TERMINALS = {
    "gnome-terminal": (["--working-directory", "{d}"], ["--tab", "--working-directory", "{d}"]),
    "konsole":        (["--workdir", "{d}"],           ["--new-tab", "--workdir", "{d}"]),
    "xfce4-terminal": (["--working-directory", "{d}"], ["--tab", "--working-directory", "{d}"]),
    "kitty":          (["--directory", "{d}"],         None),
    "alacritty":      (["--working-directory", "{d}"], None),
    "foot":           (["-D", "{d}"],                  None),
}


def has_display() -> bool:
    """The discriminator for `o`/`O`/`w`/`t` on Linux. Deliberately not
    $SSH_CONNECTION: X-forwarding gives an ssh session a usable display, and a
    local session that lost its display has nothing to open onto either. What
    matters is whether there is a desktop to talk to, not how we got here."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def launch_detached(args: list[str], cwd: str | None = None) -> None:
    """Popen, not run(): kitty, alacritty and foot do not fork, so run() would
    block the browser until the new window is closed. start_new_session keeps
    the child off our process group, so ctrl-c here never reaches it."""
    subprocess.Popen(args, cwd=cwd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     start_new_session=True)


# ------------------------------------------------------------------------- follow


def self_tty() -> str | None:
    """This terminal's tty name: the path under /dev with `/` mapped to `-`.
    MUST match _nav_tty() in nav.zsh byte for byte or self-exclusion silently
    fails and the driving terminal fights itself.

    Not a basename: Linux `/dev/pts/3` would collapse to `3`, which collides
    with the console `/dev/tty3` and makes a garbage FIFO stem. macOS
    `/dev/ttys003` has no slash left to map, so it is unchanged by this rule
    and existing state keeps working."""
    for fd in (0, 1, 2):
        try:
            name = os.ttyname(fd)
        except OSError:
            continue
        return name.removeprefix("/dev/").replace("/", "-") or None
    return None


def read_role(me: str | None) -> tuple[str, str]:
    """This window's (role, group): leader, follower or solo, and which group.

    $NAV_STATE/roles/<tty> is the single source of truth and the seam with the
    shell. It holds "<role> <group>"; a bare "leader" with no second field is
    the pre-groups format and still reads as group `default`, so role files
    written by an older version stay valid. Writing it is all this process can
    do about a role -- only a shell can register a FIFO with `zle -F` -- so
    nav.zsh's _nav_reconcile brings the live machinery in line afterwards. An
    unreadable or junk value in either field degrades to solo rather than
    costing you the browser."""
    if me is None:
        return "solo", DEFAULT_GROUP
    try:
        parts = (ROLES / me).read_text().split()
    except OSError:
        return "solo", DEFAULT_GROUP
    role = parts[0] if parts else ""
    group = parts[1] if len(parts) > 1 else DEFAULT_GROUP
    if role not in ("leader", "follower") or not group_ok(group):
        return "solo", DEFAULT_GROUP
    return role, group


def find_leader(me: str | None, group: str) -> str | None:
    """The tty leading `group`, or None. The mirror of nav.zsh's
    _nav_find_leader.

    `me` is excluded: a window asking "is there a leader for me to follow?" must
    not find itself, succeed, and demote -- that leaves nobody leading, which is
    the state the refusal exists to prevent. This globs ROLES, so it belongs on
    the key paths only, never in the redraw loop."""
    try:
        entries = sorted(ROLES.iterdir())
    except OSError:
        return None
    for q in entries:
        if q.name == me:
            continue
        if read_role(q.name) == ("leader", group):
            return q.name
    return None


def write_role(me: str | None, role: str, group: str = DEFAULT_GROUP) -> bool:
    if me is None or not group_ok(group):
        return False
    try:
        ROLES.mkdir(parents=True, exist_ok=True)
        if role == "solo":
            (ROLES / me).unlink(missing_ok=True)
        else:
            (ROLES / me).write_text(role + " " + group + "\n")
    except OSError:
        return False
    if role == "leader":
        # Exactly one leader *per group*. Enforced only here, on promotion --
        # reading a role never globs this directory, it reads one file by name.
        try:
            others = [q for q in ROLES.iterdir() if q.name != me]
        except OSError:
            others = []
        for q in others:
            # Parsed, never a prefix match on "leader": with the group in the
            # content, taking group 3 would otherwise evict group 1's leader.
            if read_role(q.name) == ("leader", group):
                try:
                    q.unlink()
                except OSError:
                    pass
    return True


def read_cwd(group: str) -> str | None:
    """Where the group's leader is. Both publishers -- publish() below and
    nav.zsh's _nav_publish -- write this file, so it is the one place a follower
    has to look."""
    try:
        return cwd_file(group).read_text().strip() or None
    except OSError:
        return None


def read_msg(dest: str, group: str) -> str:
    """The identity of the last broadcast: its generation stamp and its
    directory. A publish is an event, not a value -- a leader re-selecting the
    directory a follower has since left writes a byte-identical `cwd`, and
    without the stamp that is indistinguishable from the stale file the
    follower already declined. `self.seen` holds one of these, never a path."""
    try:
        gen = gen_file(group).read_text().strip()
    except OSError:
        gen = ""
    return gen + ":" + dest


def publish(path: Path, me: str | None, group: str) -> None:
    """Broadcast a directory to every terminal subscribed to `group`."""
    payload = (str(path) + "\n").encode()
    try:
        # sub/ too: a leader can publish into a group no follower has joined.
        sub_dir(group).mkdir(parents=True, exist_ok=True)
        cwd_file(group).write_text(str(path) + "\n")
        gen_file(group).write_text(str(time.time_ns()) + "\n")  # see read_msg()
    except OSError:
        pass
    # The group is in the path, never looked up per subscriber: this glob runs
    # on every cursor move through maybe_publish(), and deciding membership by
    # reading role files here would put a second glob plus N reads on it.
    try:
        fifos = sorted(sub_dir(group).glob("*.fifo"))
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
                # collection rides along on the write we were doing anyway --
                # and so only ever covers the group being published to.
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
        self.wake = -1

    def __enter__(self) -> "Screen":
        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, self._bail)
        signal.signal(signal.SIGWINCH, self._resized)
        # A self-pipe, because a signal alone cannot wake the read below.
        # PEP 475 restarts an interrupted syscall once the handler returns
        # normally, so _resized() would blank prev and then the process would
        # sit in os.read() until the user happened to press something -- the
        # window resized and the frame stale until then. set_wakeup_fd makes
        # the signal a readable byte, which select() can wait on alongside the
        # keyboard. key()'s existing InterruptedError branch shows this was
        # always the contract; it just never fired.
        try:
            read_fd, write_fd = os.pipe()
            os.set_blocking(read_fd, False)
            os.set_blocking(write_fd, False)
            # warn_on_full_buffer=False: the default prints to stderr, and
            # stderr here is the alt screen -- the same reason stdout is the
            # display and never a data channel. A dropped wakeup byte costs
            # one late repaint; a warning corrupts the frame.
            signal.set_wakeup_fd(write_fd, warn_on_full_buffer=False)
            self.wake = read_fd
        except (OSError, ValueError):
            self.wake = -1  # degrade to the old behaviour, never to no browser
        return self

    def __exit__(self, *exc) -> None:
        self.restore()

    def restore(self) -> None:
        sys.stdout.write("\x1b[?25h\x1b[?1049l" + RESET)
        sys.stdout.flush()
        if self.wake >= 0:
            # Unregister before closing: a signal delivered after the write end
            # is gone would print "Exception ignored" over the restored screen.
            try:
                write_fd = signal.set_wakeup_fd(-1)
                os.close(self.wake)
                if write_fd >= 0:
                    os.close(write_fd)
            except (OSError, ValueError):
                pass
            self.wake = -1
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            self.saved = None

    def suspend(self) -> None:
        """Hand the real terminal to a foreground child (nvim). Unlike
        restore(), self.saved is kept and the wakeup pipe/signal handlers
        stay armed -- this is a loan, not a shutdown, so a SIGHUP arriving
        while the child owns the terminal still finds _bail() able to
        restore correctly."""
        sys.stdout.write("\x1b[?25h\x1b[?1049l" + RESET)
        sys.stdout.flush()
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def resume(self) -> None:
        """Take the terminal back after the child exits. The tcflush
        discards whatever the child's own exit left queued -- undrained, a
        stray keystroke decodes through key() as a real key and, with no
        pending_op, can quit the browser outright."""
        tty.setcbreak(self.fd)
        termios.tcflush(self.fd, termios.TCIFLUSH)
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()
        self.prev = []  # force a full repaint -- the terminal was on loan

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
        nobody is typing: select first, and report None when it expires.

        Waiting on the wakeup pipe as well as the keyboard is what lets a
        SIGWINCH return None from here, so run() loops round and repaints at
        the new size instead of blocking until the next keypress."""
        watch = [self.fd] + ([self.wake] if self.wake >= 0 else [])
        if timeout is not None or self.wake >= 0:
            r, _, _ = select.select(watch, [], [], timeout)
            if not r:
                return None
            if self.fd not in r:
                try:  # a signal, not a key: drain it and let run() repaint
                    os.read(self.wake, 1024)
                except OSError:
                    pass
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
    # "cut" and "copy" both start with c, so the key can't be read off the
    # verb name (verb[0]) the way move/copy could -- this is still the one
    # place that maps a verb to the key that triggers it, but it is now
    # *derived* from the live keymap rather than hardcoded. Spell it out as a
    # literal again and rebinding `m` makes the hint line advertise a key that
    # no longer does anything.
    @property
    def VERB_KEYS(self) -> dict[str, str]:
        return {verb: self.keys["op_" + verb]
                for verb in ("move", "copy", "cut", "delete")}

    def __init__(self, root: Path, cfg: dict) -> None:
        self.root = root
        self.cfg = cfg
        self.colors = cfg["colors"]
        self.show_hidden = bool(cfg["behavior"]["show_hidden"])
        self.expanded: set[str] = set()
        self.marked: set[str] = set()
        self.pending_op: str | None = None  # None, "move", "copy", "cut", or "delete"
        self.cursor = 0
        self.top = 0
        self.rows: list[Row] = []
        self.me = self_tty()
        self.published: str | None = None
        self.role, self.group = read_role(self.me)
        self.seen = None  # the message a follower has already acted on
        if self.role == "solo" and bool(cfg["behavior"]["follow_default"]):
            # The old broadcast-by-default switch, read as "start this window
            # leading the default group". A saved role always wins: the config
            # only gets a say when there is no role file at all.
            write_role(self.me, "leader", DEFAULT_GROUP)
            self.role, self.group = "leader", DEFAULT_GROUP
        self.chosen: Path | None = None
        self.message = ""
        self.count_buf = ""  # digits typed so far for a pending "5j"-style count
        # action -> key, and its reverse index. run() dispatches on the action,
        # never on a literal character, which is what stops the `?` table from
        # describing a binding the dispatch no longer honours.
        self.keys = dict(cfg.get("keys") or DEFAULT_KEYS)
        self.binds = self._build_binds()
        # A panel is a *mode*, not an overlay: Screen.paint() diffs against the
        # previous frame, so anything drawn outside frame()'s return value is
        # clobbered on the next changed line. frame() and run() both dispatch
        # on self.mode, keeping one paint path and one input path.
        self.mode: str | None = None
        self.panel_top = 0
        self.panel_cursor = 0
        # None, "bind" (the next keypress becomes a binding) or "color" (typing
        # a hex value into edit_buf). Both live only while a panel is up.
        self.panel_edit: str | None = None
        self.edit_buf = ""
        self.rebuild()

    def _build_binds(self) -> dict[str, str]:
        """key -> action. Arrows come last and unconditionally: they are not
        remappable, so they cannot be stolen by a user binding."""
        binds = {key: action for action, key in self.keys.items()}
        binds.update(ARROW_KEYS)
        return binds

    # -- model ------------------------------------------------------------

    def rebuild(self) -> None:
        rows: list[Row] = []

        def walk(d: Path, depth: int) -> None:
            if depth > MAX_DEPTH:
                return
            for _name, path, is_dir in list_dir(d, self.show_hidden):
                rows.append(Row(path, depth, is_dir))
                if is_dir and self.is_open(path):
                    walk(path, depth + 1)

        walk(self.root, 0)
        self.rows = rows
        self.cursor = max(0, min(self.cursor, len(rows) - 1))

    def current(self) -> Row | None:
        return self.rows[self.cursor] if self.rows else None

    # `expanded` is keyed on str, not Path: a set of Paths would compare by a
    # richer notion of equality than the one thing we mean here, which is "the
    # same string we walked with". These three are the only way in or out.

    def is_open(self, path: Path) -> bool:
        return str(path) in self.expanded

    def open_node(self, path: Path) -> None:
        self.expanded.add(str(path))

    def close_node(self, path: Path) -> None:
        self.expanded.discard(str(path))

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
        publish(Path(target), self.me, self.group)

    def select_path(self, path: Path) -> None:
        for i, row in enumerate(self.rows):
            if row.path == path:
                self.cursor = i
                return

    def set_role(self, role: str, group: str | None = None) -> bool:
        """F and f take no argument, so they act on this window's current group
        -- falling back to `default` -- rather than yanking a window out of the
        group it joined with `n lead <g>` / `n follow <g>`."""
        group = group or self.group or DEFAULT_GROUP
        if not write_role(self.me, role, group):
            self.message = "cannot write the role file"
            return False
        self.role = role
        self.group = group
        self.published = None  # a fresh leader must state where it is
        self.seen = None       # a seen-stamp never carries across groups
        return True

    def sync_from_leader(self, force: bool = False) -> bool:
        """Follower poll. Reads the group's `cwd` rather than this terminal's
        FIFO: the shell holds that FIFO open for `zle -F` and a second reader
        would race it for the bytes. The shell keeps its own copy of the
        message, so quitting still lands the shell in the same place."""
        dest = read_cwd(self.group)
        if dest is None:
            return False
        msg = read_msg(dest, self.group)
        if msg == self.seen and not force:
            return False
        self.seen = msg  # recorded before acting, like _NAV_SEEN in nav.zsh
        target = Path(dest)
        if not target.is_dir():
            return False
        # "Already there" has to mean this directory's *contents* are on screen,
        # not merely that the cursor sits on its row. A folder under the cursor
        # is collapsed until something expands it, so testing published_dir()
        # alone left the leader's directory selected and unopened -- and stuck
        # that way, since every later message naming it took this same exit.
        # The root is never in `expanded` and is always open.
        if target == self.published_dir() and (
                target == self.root or self.is_open(target)):
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
            self.open_node(target)
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
            self.open_node(node)
        self.rebuild()
        self.select_path(target)

    def read_slot(self, screen: Screen, cols: int, height: int,
                  prompt: str) -> str | None:
        """Block for one more keypress after B/b, showing `prompt` in the hint
        line first. Returns the raw key -- the caller sorts digit from cancel
        from quit, because ctrl-c/ctrl-d must keep exiting the browser from
        inside this prompt exactly as they do at the top level; folding that
        into "anything non-digit cancels" would swallow them instead."""
        self.message = prompt
        screen.paint(self.frame(cols, height))
        return screen.key(None)

    def read_line(self, screen: Screen, cols: int, height: int,
                  prompt: str) -> str | None:
        """Block for a typed line after `prompt`, repainting the
        in-progress buffer on every keystroke via self.message -- the
        multi-character sibling of read_slot(). Returns the typed text, or
        None on ESC. Ctrl-c/ctrl-d come back raw so the caller can still
        quit the browser from inside this prompt, the same contract
        read_slot()'s callers use."""
        buf = ""
        while True:
            budget = max(0, cols - 3)
            shown = prompt + buf
            if len(shown) > budget - 1:
                room = budget - len(prompt) - 1
                shown = prompt + ("…" + buf[-(room - 1):] if room > 1 else "")
            self.message = shown + "_"
            screen.paint(self.frame(cols, height))
            key = screen.key(None)
            if key is None:
                continue  # a resize woke the wakeup pipe -- repaint, don't cancel
            if key in ("\x03", "\x04"):
                return key
            if key == "ESC":
                return None
            if key == "ENTER":
                return buf
            if key in ("\x7f", "\x08"):
                buf = buf[:-1]
            elif len(key) == 1 and key.isprintable():
                buf += key

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
            self.open_node(row.path)
            self.rebuild()

    def leave(self) -> None:
        """h -- collapse if expanded, else jump to the parent row, else re-root
        one level up. That last case is the `..` from start.txt."""
        row = self.current()
        if row is not None and row.is_dir and self.is_open(row.path):
            self.close_node(row.path)
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
        self.open_node(old)
        self.rebuild()
        self.select_path(old)

    def open_it(self, reveal: bool = False) -> None:
        """o / O -- hand the highlighted row to the desktop.

        macOS gets `open`; anything else gets the freedesktop equivalents, but
        only when there is a display to open onto -- over ssh to a headless box
        xdg-open would either fail silently or, worse, act on somebody else's
        session. Like spawn_terminal(), every unsupported case sets a message
        and returns rather than raising out of the browser."""
        row = self.current()
        if row is None:
            return
        if IS_MAC:
            args = ["open", "-R", str(row.path)] if reveal else ["open", str(row.path)]
        else:
            if not has_display():
                what = "O" if reveal else "o"
                self.message = f"no display -- {what} needs a desktop session"
                return
            args = self._linux_open_args(row.path, reveal)
            if args is None:
                self.message = "xdg-open not found"
                return
        try:
            subprocess.run(args, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
            self.message = ("revealed " if reveal else "opened ") + row.path.name
        except OSError as exc:
            self.message = f"open failed: {exc}"

    @staticmethod
    def _linux_open_args(path: Path, reveal: bool) -> list[str] | None:
        """Reveal is the awkward half: freedesktop has no `open -R`, only the
        FileManager1 D-Bus call that Nautilus, Dolphin and Thunar all implement.
        Without gdbus, opening the parent directory is the honest approximation
        -- the file manager lands in the right folder, just without the
        selection."""
        if reveal and shutil.which("gdbus"):
            uri = "file://" + quote(str(path))
            return ["gdbus", "call", "--session",
                    "--dest", "org.freedesktop.FileManager1",
                    "--object-path", "/org/freedesktop/FileManager1",
                    "--method", "org.freedesktop.FileManager1.ShowItems",
                    f"['{uri}']", ""]
        if not shutil.which("xdg-open"):
            return None
        return ["xdg-open", str(path.parent if reveal else path)]

    def edit_it(self, screen: Screen) -> None:
        """E -- open the highlighted row (file or folder) in nvim, in the
        foreground, in this terminal. Unlike open_it()'s GUI handoff or
        spawn_terminal()'s detached window, nvim needs the real tty, so the
        alt screen and raw mode step aside for it via Screen.suspend()/
        resume() rather than the Popen-and-forget pattern those two use."""
        row = self.current()
        if row is None:
            return
        if not shutil.which("nvim"):
            self.message = "nvim not found"
            return
        screen.suspend()
        try:
            subprocess.run(["nvim", str(row.path)], check=False)
        except OSError as exc:
            self.message = f"nvim failed: {exc}"
        finally:
            screen.resume()
        self.rebuild()  # nvim can rename/create/delete via netrw or :w
        self.select_path(row.path)

    def spawn_terminal(self, new_window: bool) -> None:
        """w / t -- open a new terminal window or tab already cd'd here.

        published_dir(), deliberately, and not row.path the way open_it() does:
        `o` opens the highlighted *file*, but a shell can only cd to a
        directory, so this is the same "where am I" rule the followers and
        $NAV_LASTDIR use.

        Every unsupported case sets a message and returns. This key must never
        be the one that raises out of the browser."""
        d = str(self.published_dir())
        what = "window" if new_window else "tab"
        try:
            spawned = (self._spawn_macos(d, new_window) if IS_MAC
                       else self._spawn_linux(d, new_window))
        except OSError as exc:
            self.message = f"new {what} failed: {exc}"
            return
        if spawned:
            self.message = f"new {what} in {home_short(Path(d))}"

    def _spawn_macos(self, d: str, new_window: bool) -> bool:
        """Warp by deep link, iTerm and Terminal.app by AppleScript, keyed off
        $TERM_PROGRAM. Returns False having set a message when it cannot."""
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
                return False
            args = ["osascript", "-e",
                    f'tell application "Terminal"\n'
                    f'do script "{cd}"\n'
                    f'activate\n'
                    f'end tell']
        else:
            self.message = (f"no new-{what} support for "
                            f"{term or 'this terminal (TERM_PROGRAM unset)'}")
            return False

        # `open` and `osascript` both hand off and return, so run() is fine
        # here where the Linux path needs Popen.
        subprocess.run(args, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        return True

    def _spawn_linux(self, d: str, new_window: bool) -> bool:
        """The first installed terminal from LINUX_TERMINALS, or $NAV_TERMINAL.

        $TERM_PROGRAM is a macOS convention and is unset over ssh, so this asks
        the filesystem instead of the environment: which terminal is *here*.
        An unrecognised $NAV_TERMINAL is launched bare with cwd=d -- inheriting
        the working directory is the one thing every terminal does -- while the
        known ones take an explicit flag, because gnome-terminal and konsole
        hand the request to an already-running server whose cwd is not ours."""
        what = "window" if new_window else "tab"
        if not has_display():
            return self._no(f"no display -- {what} needs a desktop session")

        override = os.environ.get("NAV_TERMINAL", "").strip()
        if override:
            exe = shlex.split(override)[0]
            if not shutil.which(exe):
                return self._no(f"$NAV_TERMINAL: {exe} not found")
            if exe not in LINUX_TERMINALS:
                if not new_window:
                    return self._no(f"no new-tab support for {exe}")
                launch_detached(shlex.split(override), cwd=d)
                return True
            name = exe
            base = shlex.split(override)
        else:
            name = next((t for t in LINUX_TERMINALS if shutil.which(t)), None)
            if name is None:
                return self._no("no supported terminal found -- "
                                "set $NAV_TERMINAL")
            base = [name]

        window_args, tab_args = LINUX_TERMINALS[name]
        flags = window_args if new_window else tab_args
        if flags is None:
            return self._no(f"{name} has no scriptable new tab -- w works")
        launch_detached(base + [a.format(d=d) for a in flags])
        return True

    def _no(self, message: str) -> bool:
        self.message = message
        return False

    def create_file(self, name: str) -> None:
        """n's second half -- create an empty file in published_dir().
        Refuses a collision rather than overwriting, same rule as
        _op_batch()'s preflight; exist_ok=False on touch() closes the
        TOCTOU gap between that check and the write."""
        why = name_ok(name)
        if why:
            self.message = why
            return
        dest = self.published_dir()
        target = dest / name
        if os.path.lexists(target):
            self.message = f"'{name}' already exists"
            return
        try:
            target.touch(exist_ok=False)
        except OSError as exc:
            self.message = f"could not create '{name}': {exc}"
            return
        self.open_node(dest)  # must run before rebuild(), or a new file
        self.rebuild()        # inside a still-collapsed folder never shows
        self.select_path(target)
        hint = "" if self.show_hidden or not name.startswith(".") \
            else f" (hidden -- {self.keys['hidden']} to show)"
        self.message = f"created {name}{hint}"

    def toggle_mark(self) -> None:
        """e -- mark or unmark the highlighted row while a move, copy, cut
        or delete is pending. Marks are path strings, same convention as
        `expanded`: independent of `self.rows`, so collapsing a marked
        folder never unmarks it. Marks are shared across all four verbs --
        switching which verb key you press keeps whatever is already
        marked."""
        if self.pending_op is None:
            self.message = "press m to move, c to copy, x to cut, d to delete"
            return
        row = self.current()
        if row is None:
            self.message = "nothing here to mark"
            return
        key = str(row.path)
        if key in self.marked:
            self.marked.discard(key)
        else:
            self.marked.add(key)
        n = len(self.marked)
        verb = self.pending_op
        letter = self.VERB_KEYS[verb]
        where = "" if verb == "delete" else " here"
        self.message = (f"{n} marked -- {letter} to {verb}{where}" if n
                        else f"{verb} mode -- e to mark, {letter} to {verb}{where}")

    def _op_batch(self, dest: Path) -> list[Path] | None:
        """The marked set, reduced to what actually needs to move/copy/cut:
        nested marks collapsed onto their ancestor (relocating/copying the
        ancestor already carries the descendant with it), and items already
        sitting in `dest` dropped. Returns None -- with self.message already
        set -- on a refusal that must not clear the marks (a bad destination,
        or a name collision), so the user can pick a different destination
        without re-marking everything. Shared by all three verbs since the
        preflight (nested-mark collapse, duplicate names, lexists collision)
        doesn't depend on which one is relocating or duplicating the batch."""
        verb = self.pending_op
        marks = sorted((Path(p) for p in self.marked), key=lambda p: len(p.parts))
        top: list[Path] = []
        for p in marks:
            if not any(p.is_relative_to(a) for a in top):
                top.append(p)
        for p in top:
            if p.is_dir() and (dest == p or dest.is_relative_to(p)):
                self.message = f"can't {verb} {p.name} into itself"
                return None
        batch = [p for p in top if p.parent != dest]
        if not batch:
            return []
        names = [p.name for p in batch]
        dup = next((n for n in names if names.count(n) > 1), None)
        if dup is not None:
            self.message = f"two marked items are both named '{dup}'"
            return None
        for p in batch:
            if os.path.lexists(dest / p.name):
                self.message = f"'{p.name}' already exists at the destination"
                return None
        return batch

    def try_op(self, screen: Screen, cols: int, height: int) -> None:
        """m/c/x, second press -- validate the batch, confirm with `y`, then
        move, copy or cut per self.pending_op. Any reply other than `y` is a
        full cancel, mirroring the B/b prompt's "anything else cancels" rule."""
        verb = self.pending_op
        dest = self.published_dir()
        batch = self._op_batch(dest)
        if batch is None:
            return
        if not batch:
            self.marked.clear()
            self.pending_op = None
            self.message = f"nothing to {verb} -- already there"
            return
        # _hint_line's own truncation drops whole segments rather than
        # slicing mid-string, so a raw message here must not rely on that --
        # "y to confirm" must never be the part a long destination path or
        # name list pushes off the end of the line. Drop the names first,
        # then -- if the path alone still doesn't fit -- elide the path from
        # the LEFT, the same rule _title_line() uses: the tail of a path is
        # the informative half.
        tail = " -- y to confirm"
        lead = f"{verb} {len(batch)} to "
        dest_str = home_short(dest)
        names = ", ".join(p.name for p in batch)
        budget = max(0, cols - 3)
        prompt = f"{lead}{dest_str} ({names}){tail}"
        if len(prompt) > budget:
            prompt = f"{lead}{dest_str}{tail}"
        if len(prompt) > budget:
            room = budget - len(lead) - len(tail)
            if room > 1:
                dest_str = "…" + dest_str[-(room - 1):]
            else:
                dest_str = ""
            prompt = f"{lead}{dest_str}{tail}"
        reply = self.read_slot(screen, cols, height, prompt)
        if reply in ("\x03", "\x04"):
            return  # ctrl-c / ctrl-d still quit from inside the prompt
        if reply != "y":
            self.marked.clear()
            self.pending_op = None
            self.message = f"{verb} cancelled"
            return
        if verb == "copy":
            self.do_copy(dest, batch)
        else:
            self.do_move(dest, batch, verb)

    def do_move(self, dest: Path, batch: list[Path], verb: str = "move") -> None:
        """The moves themselves -- shared by move and cut, which are the same
        filesystem operation under two keys (per the user's explicit choice
        of a real `x` binding over treating cut as a plain alias for `m`).
        `verb` only steers the message text ("moved"/"cut"). dest is always
        passed as the directory, never joined with a name: shutil.move
        resolves that itself, and joining it ourselves risks falling through
        to a raw os.rename that would silently replace whatever the
        collision preflight already checked for."""
        action = "moved" if verb == "move" else "cut"
        moved = 0
        for p in batch:
            try:
                shutil.move(str(p), str(dest))
            except (OSError, shutil.Error) as exc:
                self.message = f"{action} {moved} of {len(batch)} -- failed on {p.name}: {exc}"
                break
            moved += 1
        else:
            self.message = f"{action} {moved} item{'s' if moved != 1 else ''} to {home_short(dest)}"
        self.marked.clear()
        self.pending_op = None
        self.open_node(dest)
        self.rebuild()
        self.select_path(dest)

    def do_copy(self, dest: Path, batch: list[Path]) -> None:
        """The copies themselves. Unlike shutil.move, shutil.copytree refuses
        an existing target directory rather than merging into it -- the
        collision preflight already guarantees dest/name doesn't exist, so
        that refusal never fires here, but it does mean a directory needs the
        name joined explicitly rather than handed the bare dest the way
        shutil.copy2 (file case) accepts."""
        copied = 0
        for p in batch:
            try:
                if p.is_dir():
                    shutil.copytree(str(p), str(dest / p.name))
                else:
                    shutil.copy2(str(p), str(dest))
            except (OSError, shutil.Error) as exc:
                self.message = f"copied {copied} of {len(batch)} -- failed on {p.name}: {exc}"
                break
            copied += 1
        else:
            self.message = f"copied {copied} item{'s' if copied != 1 else ''} to {home_short(dest)}"
        self.marked.clear()
        self.pending_op = None
        self.open_node(dest)
        self.rebuild()
        self.select_path(dest)

    def _delete_batch(self) -> list[Path]:
        """The marked set, reduced to what actually needs deleting: nested
        marks collapsed onto their ancestor, same rule as _op_batch's first
        step. There is no destination for delete, so none of _op_batch's
        collision/lexists checks apply -- this is deliberately not a call to
        _op_batch with dest=None."""
        marks = sorted((Path(p) for p in self.marked), key=lambda p: len(p.parts))
        top: list[Path] = []
        for p in marks:
            if not any(p.is_relative_to(a) for a in top):
                top.append(p)
        return top

    def try_delete(self, screen: Screen, cols: int, height: int) -> None:
        """d, second press -- unlike move/copy/cut's one whole-batch prompt,
        delete confirms per item: `y` deletes this one and asks again for the
        next, `Y` deletes this one and every item still left in the batch
        without asking again. Anything else is still try_op's "anything but y
        cancels" rule -- it stops the rest of the batch, not just this item,
        since a delete has no destination left to go clean up by hand."""
        batch = self._delete_batch()
        if not batch:
            self.marked.clear()
            self.pending_op = None
            self.message = "nothing to delete"
            return
        deleted = 0
        auto = False
        for p in batch:
            if not auto:
                tail = " -- y/N, Y for all"
                lead = "delete "
                budget = max(0, cols - 3)
                prompt = f"{lead}{p.name}?{tail}"
                if len(prompt) > budget:
                    room = budget - len(lead) - len(tail) - 1
                    name = "…" + p.name[-(room - 1):] if room > 1 else ""
                    prompt = f"{lead}{name}?{tail}"
                reply = self.read_slot(screen, cols, height, prompt)
                if reply in ("\x03", "\x04"):
                    # Unlike try_op's ctrl-c (nothing mutated yet at that
                    # point), earlier items in this loop may already be
                    # gone from disk -- clear up the same as a cancel so
                    # `marked`/`pending_op` and the tree don't go stale.
                    self.message = f"deleted {deleted} of {len(batch)} -- cancelled"
                    self.marked.clear()
                    self.pending_op = None
                    self.rebuild()
                    return
                if reply == "Y":
                    auto = True
                elif reply != "y":
                    self.message = f"deleted {deleted} of {len(batch)} -- cancelled"
                    self.marked.clear()
                    self.pending_op = None
                    self.rebuild()
                    return
            try:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(str(p))
                else:
                    p.unlink()  # rm semantics: removes a symlink itself, never its target
            except (OSError, shutil.Error) as exc:
                self.message = f"deleted {deleted} of {len(batch)} -- failed on {p.name}: {exc}"
                self.marked.clear()
                self.pending_op = None
                self.rebuild()
                return
            deleted += 1
        self.message = f"deleted {deleted} item{'s' if deleted != 1 else ''}"
        self.marked.clear()
        self.pending_op = None
        self.rebuild()

    # -- render -----------------------------------------------------------

    def frame(self, cols: int, height: int) -> list[str]:
        if self.mode:
            return self.panel_frame(cols, height)
        border = fg(self.colors["border"])
        width = max(30, min(cols, 200))
        inner = width - 2
        body_h = max(3, height - 4)

        self._scroll_into_view(body_h)

        lines = [self._title_line(inner)]
        for i in range(body_h):
            idx = self.top + i
            if idx >= len(self.rows):
                lines.append(f"{border}│{RESET}{' ' * inner}{border}│{RESET}")
                continue
            lines.append(self.render_row(idx, inner, border))
        lines.append(f"{border}╰{'─' * inner}╯{RESET}")
        lines.append(self._hint_line(cols))

        while len(lines) < height:
            lines.append("")
        return lines[:height]

    def _scroll_into_view(self, body_h: int) -> None:
        """Keep the cursor inside the viewport, with a little breathing room.
        Mutates self.top, so it has to run before anything reads it -- which is
        why frame() calls it above the row loop rather than beside the other
        line builders."""
        if self.cursor < self.top + 2:
            self.top = max(0, self.cursor - 2)
        if self.cursor > self.top + body_h - 3:
            self.top = self.cursor - body_h + 3
        self.top = max(0, min(self.top, max(0, len(self.rows) - body_h)))

    def _title_line(self, inner: int) -> str:
        """The top border: the role flag, and the root path elided from the
        LEFT -- the tail of a path is the informative half -- so that the title
        can never widen the box."""
        c = self.colors
        border, dim, accent = fg(c["border"]), fg(c["dim"]), fg(c["accent"])
        # Named groups are shown, the default one is not: the browser is
        # otherwise the one place that cannot say which group you are in, and
        # `room` below is derived from len(flag), so the box still cannot widen.
        tag = "" if self.group == DEFAULT_GROUP else " " + self.group
        if self.role == "leader":
            flag, flag_fg = " leading" + tag + " ", accent
        elif self.role == "follower":
            flag, flag_fg = " following" + tag + " ", dim
        else:
            flag, flag_fg = "", ""
        room = inner - 3 - len(flag)
        raw = home_short(self.root)
        if len(raw) > room:
            raw = "…" + raw[-(room - 1):] if room > 1 else ""
        title = " " + raw + " "
        # inner + 2 printable columns, matching the body rows and the bottom
        # border. `room` above already caps len(title), so this cannot widen.
        fill = max(0, inner - 1 - len(title) - len(flag))
        return (f"{border}╭─{accent}{BOLD}{title}{RESET}"
                f"{flag_fg}{flag}{border}{'─' * fill}╮{RESET}")

    def _hint_line(self, cols: int) -> str:
        """A transient message, or the key hints -- dropping whole hint
        segments rather than slicing a word in half."""
        if self.message:
            hint = self.message
        elif self.pending_op:
            verb = self.pending_op
            letter = self.VERB_KEYS[verb]
            n = len(self.marked)
            mark = self.keys["mark"]
            where = "" if verb == "delete" else " here"
            hint = (f"{verb} mode: {n} marked -- {mark} to mark, {letter} to {verb}{where}"
                    if n else f"{verb} mode -- {mark} to mark, {letter} to {verb}{where}")
        else:
            # Deliberately short. The full list used to live here and was
            # truncated segment by segment, so on a narrow terminal most of the
            # keys silently vanished; the complete table is one keypress away
            # behind `?` instead, which is also why the footer stays ONE line
            # and frame()'s `body_h = height - 4` needs no adjusting.
            # Order matters: the loop below drops the second-to-last segment
            # first, so what survives a narrow terminal is the front of this
            # list plus `q quit`. `? keys` sits near the front deliberately --
            # it is the one segment that can replace all the others.
            keys = self.keys
            segs = [f"{keys['leave']}{keys['down']}{keys['up']}{keys['enter']} move",
                    f"{keys['panel_keys']} keys",
                    "↵ cd here",
                    f"{keys['panel_settings']} settings",
                    "q quit"]
            hint = " · ".join(segs)
            while segs and len(hint) > cols - 3:
                segs.pop(-2 if len(segs) > 1 else 0)
                hint = " · ".join(segs)
        return f"  {fg(self.colors['dim'])}{hint[:max(0, cols - 3)]}{RESET}"

    # -- panels -----------------------------------------------------------
    #
    # A panel is a whole frame, never an overlay -- see self.mode. Every body
    # builder below is a pure function returning (plain, styled) pairs: the
    # plain half is what truncation and gap-padding measure, the same trick
    # render_row() uses, and being pure is what lets them be exercised without
    # a terminal (nav.py refuses to run unless both fds are TTYs).
    #
    # The panels that carry a cursor keep their bodies FLAT -- no headings, no
    # blank rows -- so that panel_cursor is a plain index into the body list.
    # `keys` is the one grouped panel and the one with no cursor.

    PANEL_TITLES = {"keys": "keys", "settings": "settings", "colors": "colours",
                    "binds": "keybinds", "bookmarks": "bookmarks"}
    SETTINGS_ROWS = [("colors", "colours"), ("binds", "keybinds"),
                     ("bookmarks", "bookmarks")]
    CURSOR_PANELS = frozenset({"settings", "colors", "binds", "bookmarks"})
    SUBMENUS = frozenset({"colors", "binds", "bookmarks"})

    def _panel_content(self) -> tuple[list[tuple[str, str]], str]:
        """The current panel's body and its default footer. Both the renderer
        and _panel_key() go through here, so the cursor can never run past the
        end of a body that only the renderer knew the length of."""
        if self.panel_edit == "bind":
            return self._binds_body(), "press any key · esc cancels"
        if self.panel_edit == "color":
            return self._colors_body(), "type #rrggbb · ↵ save · esc cancels"
        return {
            "keys": lambda: (self._keys_body(), "esc back · j/k scroll"),
            "settings": lambda: (self._settings_body(), "↵ open · esc back"),
            "colors": lambda: (self._colors_body(), "↵ edit · esc back"),
            "binds": lambda: (self._binds_body(), "↵ rebind · esc back"),
            "bookmarks": lambda: (self._bookmarks_body(), "↵ go there · esc back"),
        }[self.mode]()

    def panel_frame(self, cols: int, height: int) -> list[str]:
        body, footer = self._panel_content()
        cursor = self.panel_cursor if self.mode in self.CURSOR_PANELS else None
        if cursor is not None:
            cursor = max(0, min(cursor, max(0, len(body) - 1)))
        return self._panel(cols, height, self.PANEL_TITLES[self.mode],
                           body, self.message or footer, cursor)

    def _panel(self, cols: int, height: int, title: str,
               body: list[tuple[str, str]], footer: str,
               cursor: int | None) -> list[str]:
        """The one panel renderer. Returns a full height-length line list, the
        same shape frame() does, because paint() will clobber anything else."""
        c = self.colors
        border = fg(c["border"])
        width = max(30, min(cols, 200))
        inner = width - 2
        body_h = max(3, height - 4)  # title, body, bottom border, footer

        if cursor is not None:
            self.panel_top = min(self.panel_top, cursor)
            self.panel_top = max(self.panel_top, cursor - body_h + 1)
        self.panel_top = max(0, min(self.panel_top, max(0, len(body) - body_h)))

        text = " " + title + " "
        fill = max(0, inner - 1 - len(text))
        lines = [f"{border}╭─{fg(c['accent'])}{BOLD}{text}{RESET}"
                 f"{border}{'─' * fill}╮{RESET}"]
        for i in range(body_h):
            idx = self.panel_top + i
            if idx >= len(body):
                lines.append(f"{border}│{RESET}{' ' * inner}{border}│{RESET}")
                continue
            plain, styled = body[idx]
            if len(plain) > inner - 2:
                plain = plain[:max(0, inner - 3)] + "…"
                styled = f"{fg(c['file'])}{plain}{RESET}"
            gap = max(0, inner - 2 - len(plain))
            row = f" {styled}{' ' * gap} "
            if cursor is not None and idx == cursor:
                row = f"{bg(c['selected_bg'])}{row}{RESET}"
            lines.append(f"{border}│{RESET}{row}{border}│{RESET}")
        lines.append(f"{border}╰{'─' * inner}╯{RESET}")
        # Say when the panel is taller than the terminal. Without this the keys
        # table just stops at whatever the last visible row is and reads as the
        # complete list -- the exact failure the old truncated hint line had.
        arrows = ("↑" if self.panel_top else "") + (
            "↓" if self.panel_top + body_h < len(body) else "")
        if arrows:
            footer += f" · {arrows} more"
        lines.append(f"  {fg(c['dim'])}{footer[:max(0, cols - 3)]}{RESET}")

        while len(lines) < height:
            lines.append("")
        return lines[:height]

    def key_label(self, action: str | None, key: str, alias: str) -> str:
        """What a key prints in a table: the *live* binding for a remappable
        action, the literal for a fixed one, plus its arrow alias."""
        label = self.keys.get(action, key) if action else key
        return f"{label} {alias}" if alias else label

    def _keys_body(self) -> list[tuple[str, str]]:
        c = self.colors
        dim, accent, plainc = fg(c["dim"]), fg(c["accent"]), fg(c["file"])
        out: list[tuple[str, str]] = []
        for i, (section, rows) in enumerate(KEY_SECTIONS):
            if i:
                out.append(("", ""))
            out.append((section, f"{dim}{section}{RESET}"))
            labels = [self.key_label(a, k, al) for a, k, al, _d in rows]
            w = max(len(label) for label in labels)
            for label, (_a, _k, _al, desc) in zip(labels, rows):
                pad = label.ljust(w)
                out.append((f"  {pad}   {desc}",
                            f"  {accent}{pad}{RESET}   {plainc}{desc}{RESET}"))
        return out

    def _settings_body(self) -> list[tuple[str, str]]:
        c = self.colors
        accent, dim = fg(c["accent"]), fg(c["dim"])
        saved = sum(1 for slot in "0123456789" if read_bookmark(slot) is not None)
        counts = {"colors": f"{len(self.colors)} colours",
                  "binds": f"{len(self.keys)} bound",
                  "bookmarks": f"{saved} of 10 set"}
        out = []
        for panel, label in self.SETTINGS_ROWS:
            note = counts[panel]
            out.append((f"  {label.ljust(11)}{note}",
                        f"  {accent}{label.ljust(11)}{RESET}{dim}{note}{RESET}"))
        return out

    def _colors_body(self) -> list[tuple[str, str]]:
        c = self.colors
        accent, dim = fg(c["accent"]), fg(c["dim"])
        out = []
        for i, name in enumerate(DEFAULTS["colors"]):
            value = str(c.get(name, DEFAULTS["colors"][name]))
            if self.panel_edit == "color" and i == self.panel_cursor:
                shown = (self.edit_buf + "_").ljust(10)
                # No swatch while typing: a half-typed hex would render as the
                # fallback colour and look like it had already been applied.
                out.append((f"  {name.ljust(12)}{shown}",
                            f"  {accent}{name.ljust(12)}{RESET}{accent}{shown}{RESET}"))
                continue
            # The swatch is the whole point: hex in a config file is unreadable
            # until you see it next to the colour it actually produces.
            out.append((f"  {name.ljust(12)}{value.ljust(10)}████",
                        f"  {accent}{name.ljust(12)}{RESET}{dim}{value.ljust(10)}"
                        f"{RESET}{fg(value)}████{RESET}"))
        return out

    def _binds_body(self) -> list[tuple[str, str]]:
        c = self.colors
        accent, plainc = fg(c["accent"]), fg(c["file"])
        out = []
        for i, (action, key) in enumerate(self.keys.items()):
            desc = ACTION_DESC[action]
            if self.panel_edit == "bind" and i == self.panel_cursor:
                out.append((f"  _    press a key for “{desc}”",
                            f"  {accent}_    press a key for “{desc}”{RESET}"))
                continue
            out.append((f"  {key.ljust(4)}{desc}",
                        f"  {accent}{key.ljust(4)}{RESET}{plainc}{desc}{RESET}"))
        return out

    def _rebind(self, key: str) -> None:
        """The keypress that lands on an armed keybinds row."""
        self.panel_edit = None
        action = list(self.keys)[self.panel_cursor]
        if key == "ESC":
            self.message = "unchanged"
            return
        why = key_ok(key, action, self.keys)
        if why:
            # Refused, not resolved: two actions on one key would be settled
            # silently by the order of run()'s elif chain.
            self.message = why
            return
        self.keys[action] = key
        self.binds = self._build_binds()  # rebuild, don't patch: the old key
        if not write_config_value("keys", action, key):  # must stop working at once
            self.message = f"{key} works now, but config.toml could not be written"
        else:
            self.message = f"{ACTION_DESC[action]} is now {key}{SESSION_ONLY}"

    def _recolor(self) -> None:
        """↵ on a colour being typed. Applied live -- self.colors is the dict
        every fg()/bg() call reads, so there is nothing to relaunch."""
        value = self.edit_buf
        name = list(DEFAULTS["colors"])[self.panel_cursor]
        self.panel_edit, self.edit_buf = None, ""
        if len(value) != 7 or not value.startswith("#") or \
                any(ch not in "0123456789abcdefABCDEF" for ch in value[1:]):
            self.message = f"{value} is not a #rrggbb colour"
            return
        self.colors[name] = value
        if write_config_value("colors", name, value):
            self.message = f"{name} is now {value}{SESSION_ONLY}"
        else:
            self.message = f"{name} is {value} for now -- config.toml is unwritable"

    def _bookmarks_body(self) -> list[tuple[str, str]]:
        c = self.colors
        accent, dim, plainc = fg(c["accent"]), fg(c["dim"]), fg(c["file"])
        out = []
        for slot in "0123456789":
            target = read_bookmark(slot)
            if target is None:
                out.append((f"  {slot}   —", f"  {accent}{slot}{RESET}   {dim}—{RESET}"))
                continue
            # A bookmarked directory can be deleted under you; say so here
            # rather than only when `b` fails on it.
            gone = "" if target.is_dir() else "   (missing)"
            shown = home_short(target)
            out.append((f"  {slot}   {shown}{gone}",
                        f"  {accent}{slot}{RESET}   {plainc}{shown}{RESET}"
                        f"{dim}{gone}{RESET}"))
        return out

    def _panel_open(self) -> None:
        """↵ inside a panel. Only the settings menu and the bookmarks list do
        anything with it; colours and keybinds become editable in a later pass
        and say so rather than silently ignoring the key."""
        if self.mode == "settings":
            self.mode = self.SETTINGS_ROWS[self.panel_cursor][0]
            self.panel_top = self.panel_cursor = 0
        elif self.mode == "bookmarks":
            slot = "0123456789"[self.panel_cursor]
            target = read_bookmark(slot)
            if target is None:
                self.message = f"no bookmark {slot}"
            elif not target.is_dir():
                self.message = f"bookmark {slot} no longer exists: {target}"
            else:
                # Same two steps `b` takes, then out of the panel: jumping
                # somewhere and leaving the list covering it would be a lie.
                self.reveal_path(target)
                self.mode = None
                self.message = f"went to bookmark {slot}"
                self.maybe_publish()
        elif self.mode == "binds":
            self.panel_edit = "bind"
        elif self.mode == "colors":
            self.panel_edit = "color"
            self.edit_buf = "#"

    def _panel_key(self, key: str) -> bool:
        """One keypress while a panel is up. Returns True only for the keys
        that quit the browser outright.

        `q` still quits from in here, and `esc` is what closes a panel. That
        is the documented invariant kept deliberately rather than adopting the
        pager convention: `q` is the one key in this file with no second
        meaning anywhere, and ESC already carries the context-sensitive one."""
        if key in ("\x03", "\x04"):
            return True

        # An armed row swallows the keypress first, so that j/k/enter land in
        # the edit rather than in the panel's own navigation. `q` is the one
        # exception, tested above the bind branch: it is in RESERVED_KEYS, so
        # _rebind() would only ever refuse it -- swallowing it there bought
        # nothing and cost the documented "q always quits" invariant. The
        # colour prompt below keeps the swallow, because there `q` is a
        # character being typed into a text field, not a key being pressed.
        if key == "q" and self.panel_edit != "color":
            return True
        if self.panel_edit == "bind":
            self._rebind(key)
            return False
        if self.panel_edit == "color":
            if key == "ESC":
                self.panel_edit, self.edit_buf = None, ""
                self.message = "unchanged"
            elif key == "ENTER":
                self._recolor()
            elif key in ("\x7f", "\x08"):  # both backspaces: a terminal sends
                # DEL or BS depending on its erase setting, and one of the two
                # silently doing nothing in a typing prompt is maddening.
                # Never past the leading '#'.
                self.edit_buf = self.edit_buf[:1] or "#"
            elif len(key) == 1 and key.isprintable() and len(self.edit_buf) < 7:
                self.edit_buf += key
            return False

        if key == "ESC":
            # One level at a time: a submenu falls back to the settings menu,
            # the settings menu (and the keys table) back to the tree.
            self.mode = "settings" if self.mode in self.SUBMENUS else None
            self.panel_top = self.panel_cursor = 0
            return False

        body, _footer = self._panel_content()
        last = max(0, len(body) - 1)
        action = self.binds.get(key)
        if self.mode == "bookmarks" and key in "0123456789":
            # The same `b`+digit idiom, reused where the slots are on screen --
            # and the concrete reason this handler has to sit above run()'s
            # count buffer, which otherwise eats every digit before we see it.
            self.panel_cursor = int(key)
            self._panel_open()
        elif action == "down":
            # The keys table has no cursor, so j/k scroll it directly; every
            # other panel has a flat body and moves a real selection.
            if self.mode in self.CURSOR_PANELS:
                self.panel_cursor = min(self.panel_cursor + 1, last)
            else:
                self.panel_top = min(self.panel_top + 1, last)
        elif action == "up":
            if self.mode in self.CURSOR_PANELS:
                self.panel_cursor = max(0, self.panel_cursor - 1)
            else:
                self.panel_top = max(0, self.panel_top - 1)
        elif action == "top":
            self.panel_cursor = self.panel_top = 0
        elif action == "bottom":
            self.panel_cursor = self.panel_top = last
        elif key == "ENTER":
            self._panel_open()
        elif action in ("panel_keys", "panel_settings"):
            # Pressing the door again closes it, the way `?` usually behaves.
            want = "keys" if action == "panel_keys" else "settings"
            self.mode = None if self.mode == want else want
            self.panel_top = self.panel_cursor = 0
        return False

    def render_row(self, idx: int, inner: int, border: str) -> str:
        c = self.colors
        row = self.rows[idx]
        selected = idx == self.cursor
        caret = f"{fg(c['accent'])}❯{RESET}" if selected else " "
        # clamp the indent so very deep nesting can't drive text_w negative
        pad = "  " * min(row.depth, max(0, (inner - 12) // 2))
        marked = str(row.path) in self.marked
        mark_plain = "✓ " if marked else ""
        mark_styled = f"{fg(c['accent'])}✓{RESET} " if marked else ""
        if row.is_dir:
            tri = "▾" if self.is_open(row.path) else "▸"
            name = f"{mark_styled}{fg(c['dim'])}{tri} {fg(c['dir'])}{row.path.name}/{RESET}"
            plain = f"{mark_plain}{tri} {row.path.name}/"
        else:
            name = f"{mark_styled}  {fg(c['file'])}{row.path.name}{RESET}"
            plain = f"{mark_plain}  {row.path.name}"

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

            if self.mode:
                # Above the count buffer on purpose: that block eats every
                # digit, and the bookmarks panel selects rows by digit.
                if self._panel_key(key):
                    return  # only q / ctrl-c / ctrl-d get here
                continue

            if key.isdigit() and (key != "0" or self.count_buf):
                # "0" alone isn't a count (nothing is bound to a bare 0 here);
                # only "0" *after* a leading digit extends it, e.g. "10j".
                self.count_buf += key
                self.message = self.count_buf
                continue
            count = int(self.count_buf) if self.count_buf else 1
            self.count_buf = ""

            # Dispatch on the resolved action, never on the literal character:
            # this is what keeps the `?` table and the keys that actually work
            # from being two different lists. The fixed keys below it are the
            # rows KEY_SECTIONS marks with action None.
            action = self.binds.get(key)

            if key == "q":
                return
            elif key == "ESC":
                # Esc backs out of move/copy/cut mode without touching the
                # marked files -- same as pressing the verb key with nothing
                # marked, just reachable without emptying the marks first.
                # Outside a pending op it's still an alias for q.
                if self.pending_op:
                    verb = self.pending_op
                    self.marked.clear()
                    self.pending_op = None
                    self.message = f"{verb} cancelled"
                else:
                    return
            elif key == "ENTER":
                self.chosen = self.published_dir()
                return
            elif action == "down":
                self.move(count)
            elif action == "up":
                self.move(-count)
            elif action == "enter":
                self.enter()
            elif action == "leave":
                self.leave()
            elif action == "top":
                self.cursor = 0
            elif action == "bottom":
                self.cursor = max(0, len(self.rows) - 1)
            elif action == "open":
                self.open_it()
            elif action == "reveal":
                self.open_it(reveal=True)
            elif action == "edit":
                self.edit_it(screen)
            elif action in ("window", "tab"):
                self.spawn_terminal(new_window=(action == "window"))
                # A new tab lands on top of this session and no SIGWINCH fires,
                # so paint()'s diff would keep a stale frame. Blank prev the way
                # _resized() does and repaint whole.
                screen.prev = []
            elif action in ("panel_keys", "panel_settings"):
                self.mode = "keys" if action == "panel_keys" else "settings"
                self.panel_top = self.panel_cursor = 0
            elif action == "lead":
                if self.role == "leader":
                    if self.set_role("solo"):
                        self.message = "stopped leading"
                elif self.set_role("leader"):
                    self.message = ("leading group " + self.group
                                    + " — its followers track this window")
            elif action == "follow":
                if self.role == "follower":
                    if self.set_role("solo"):
                        self.message = "stopped following"
                elif find_leader(self.me, self.group or DEFAULT_GROUP) is None:
                    # Promotion only. The role file is shared with the shell, so
                    # an unguarded `f` would recreate the leaderless follower
                    # `n follow` now refuses. A message and a return, never a
                    # raise: these keys must not be the ones that kill the
                    # browser.
                    self.message = ("no leader for group "
                                    + (self.group or DEFAULT_GROUP)
                                    + " — press F in the window that should lead")
                elif self.set_role("follower"):
                    self.message = "following the leader of group " + self.group
                    self.sync_from_leader(force=True)  # `f` means "sync me now"
            elif action == "bookmark_set":
                slot = self.read_slot(screen, cols, height,
                                       "bookmark: press a digit 0-9")
                if slot in ("\x03", "\x04"):
                    return  # ctrl-c / ctrl-d still quit from inside the prompt
                elif slot is None or slot not in "0123456789":
                    self.message = "bookmark cancelled"
                elif write_bookmark(slot, self.published_dir()):
                    self.message = (f"bookmarked {slot} → "
                                     f"{home_short(self.published_dir())}")
                else:
                    self.message = "could not write the bookmark"
            elif action == "bookmark_go":
                slot = self.read_slot(screen, cols, height,
                                       "go to bookmark: press a digit 0-9")
                if slot in ("\x03", "\x04"):
                    return  # ctrl-c / ctrl-d still quit from inside the prompt
                elif slot is None or slot not in "0123456789":
                    self.message = "bookmark cancelled"
                else:
                    target = read_bookmark(slot)
                    if target is None:
                        self.message = f"no bookmark {slot}"
                    elif not target.is_dir():
                        self.message = (f"bookmark {slot} no longer exists: "
                                         f"{target}")
                    else:
                        self.reveal_path(target)
                        self.message = f"went to bookmark {slot}"
            elif action in ("op_move", "op_copy", "op_cut", "op_delete"):
                verb = action[3:]
                if self.pending_op != verb:
                    # Entering fresh, or switching verb mid-mark -- either way
                    # the current marks (if any) carry over unchanged.
                    self.pending_op = verb
                    n = len(self.marked)
                    mark = self.keys["mark"]
                    where = "" if verb == "delete" else " here"
                    self.message = (f"{n} marked -- {key} to {verb}{where}" if n
                                     else f"{verb} mode -- {mark} to mark, {key} to {verb}{where}")
                elif not self.marked:
                    self.pending_op = None
                    self.message = f"{verb} cancelled -- nothing marked"
                elif verb == "delete":
                    self.try_delete(screen, cols, height)
                else:
                    self.try_op(screen, cols, height)
            elif action == "new_file":
                name = self.read_line(screen, cols, height, "new file: ")
                if name in ("\x03", "\x04"):
                    return  # ctrl-c / ctrl-d still quit from inside the prompt
                elif not name:
                    self.message = "new file cancelled"
                else:
                    self.create_file(name)
            elif action == "mark":
                self.toggle_mark()
            elif action == "hidden":
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
