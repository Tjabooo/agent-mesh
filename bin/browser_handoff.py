#!/usr/bin/env python3
"""browser-handoff: your Firefox windows follow you between machines.

Each machine keeps the others up to date with what it has open (every tab with its back/forward
history, scroll position and typed form text). On another machine, `browser-handoff pick` (or
`continue HOST`) opens those windows there, exactly where you left them.

  browser-handoff pick               menu (rofi): continue from one of the other machines
  browser-handoff continue [HOST]    open HOST's windows here (newest machine if no HOST)
           [--window N]              only its Nth window
  browser-handoff list               what each machine had open, and how old that is
  browser-handoff push [--force]     send this machine's windows to the others now
  browser-handoff watch              the daemon: sends changes as Firefox saves them, and right
                                     before this machine goes to sleep
  browser-handoff status             profile, Firefox hook, daemon and last sends
  browser-handoff install-firefox    how to install the Firefox hook (needs root once)

How it works: Firefox saves its session to sessionstore-backups/recovery.jsonlz4 every 15 s.
The daemon reads it, keeps a small copy (no cookies, no sessionStorage) and copies it over SSH to
~/.local/state/browser-handoff/peers/<this host>.json on every machine in hosts.json. Restoring
hands the windows to Firefox's own session restore through a small privileged hook
(firefox/handoff.sys.mjs, loaded by Firefox's autoconfig), the same code Firefox uses after a crash.
Without the hook, `continue` still opens the same pages, just not at the same scroll position.
`agent-mesh sync` also sends (the only sender on Windows) and installs the daemon on Linux.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HOME = Path.home()
WINDOWS = sys.platform == 'win32'
HOST = socket.gethostname().split('.')[0].lower()
MESH = Path(__file__).resolve().parent.parent
STATE = HOME / '.local' / 'state' / 'browser-handoff'
PEERS = STATE / 'peers'
REMOTE_PEERS = '.local/state/browser-handoff/peers'
POLL = 5             # seconds between looks at Firefox's session file
PUSH_EVERY = 20      # at most one send per this many seconds while browsing
WINDOWS_EVERY = 300  # Windows peers get at most one send per 5 minutes (their sshd is touchy)
BACKOFF = 120        # a peer that didn't answer is retried after this long
MAX_ENTRIES = 25     # back/forward history kept per tab
MAX_ICON = 4096      # favicons as data: URLs are kept only when small
BLANK = {'about:blank', 'about:newtab', 'about:home', 'about:privatebrowsing'}
PICK_THEME = HOME / '.config' / 'rofi' / 'power.rasi'
UNIT = 'browser-handoff.service'


def log(msg):
    print(f'{time.strftime("%H:%M:%S")} {msg}', file=sys.stderr, flush=True)


# ---------- reading Firefox ----------
def unlz4(data):
    """Decode one LZ4 block (what mozLz4 files hold)."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        token = data[i]
        i += 1
        length = token >> 4
        if length == 15:
            while True:
                b = data[i]
                i += 1
                length += b
                if b != 255:
                    break
        out += data[i:i + length]
        i += length
        if i >= n:
            break
        offset = data[i] | data[i + 1] << 8
        i += 2
        length = token & 15
        if length == 15:
            while True:
                b = data[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4
        start = len(out) - offset
        if offset >= length:
            out += out[start:start + length]
        else:  # overlapping copy: repeat the last `offset` bytes
            chunk = out[start:]
            out += (chunk * (length // offset + 1))[:length]
    return bytes(out)


def read_mozlz4(path):
    raw = Path(path).read_bytes()
    if raw[:8] != b'mozLz40\0':
        raise ValueError(f'{path}: not a mozLz4 file')
    return json.loads(unlz4(raw[12:]))


def profile_roots():
    if WINDOWS:
        appdata = Path(os.environ.get('APPDATA', HOME / 'AppData' / 'Roaming'))
        return [appdata / 'Mozilla' / 'Firefox' / 'Profiles']
    return [HOME / '.config' / 'mozilla' / 'firefox', HOME / '.mozilla' / 'firefox']


def session_file():
    """The newest session file of any Firefox profile: the one in use."""
    best = None
    for root in profile_roots():
        if not root.is_dir():
            continue
        for profile in root.iterdir():
            for f in (profile / 'sessionstore-backups' / 'recovery.jsonlz4', profile / 'sessionstore.jsonlz4'):
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                if best is None or mtime > best[0]:
                    best = (mtime, f, profile)
    return (best[1], best[2]) if best else (None, None)


def firefox_open(profile):
    if WINDOWS:
        return (profile / 'parent.lock').exists()
    return os.path.lexists(profile / 'lock')


def small_entry(e):
    out = {k: e[k] for k in ('url', 'title', 'triggeringPrincipal_base64', 'principalToInherit_base64') if k in e}
    if 'url' not in out:
        return None
    return out


def small_tab(t):
    entries = t.get('entries') or []
    index = t.get('index', len(entries))
    if not entries or t.get('hidden'):
        return None
    index = min(max(index, 1), len(entries))
    start = max(0, index - MAX_ENTRIES)  # keep the history leading up to the current page
    kept = [small_entry(e) for e in entries[start:index + 5]]
    if None in kept:
        return None
    tab = {'entries': kept, 'index': index - start}
    if len(kept) == 1 and kept[0]['url'] in BLANK:
        return None
    for k in ('scroll', 'formdata', 'pinned', 'groupId', 'lastAccessed'):
        if t.get(k):
            tab[k] = t[k]
    icon = t.get('image')
    if isinstance(icon, str) and len(icon) <= MAX_ICON:
        tab['image'] = icon
    return tab


def small_window(w):
    tabs, selected = [], 1
    for i, t in enumerate(w.get('tabs') or [], 1):
        tab = small_tab(t)
        if tab:
            tabs.append(tab)
            if i == w.get('selected'):
                selected = len(tabs)
    if not tabs:
        return None
    win = {'tabs': tabs, 'selected': selected}
    used = {t.get('groupId') for t in tabs}
    groups = [g for g in w.get('groups') or [] if g.get('id') in used]
    if groups:
        win['groups'] = groups
    return win


def snapshot():
    """This machine's windows, small. None when there's no Firefox profile."""
    path, profile = session_file()
    if not path:
        return None
    session = read_mozlz4(path)
    windows = [w for w in (small_window(w) for w in session.get('windows') or []) if w]
    saved = (session.get('session') or {}).get('lastUpdate') or path.stat().st_mtime * 1000
    return {'v': 1, 'host': HOST, 'browser': 'firefox', 'saved': int(saved) / 1000,
            'open': firefox_open(profile), 'windows': windows}


def digest(snap):
    return hashlib.sha256(json.dumps(snap.get('windows'), sort_keys=True).encode()).hexdigest()[:16]


# ---------- state ----------
def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


# ---------- talking to peers ----------
def hosts():
    for p in (MESH / 'hosts.json', HOME / '.config' / 'archfleet' / 'hosts.json', HOME / '.config' / 'cost' / 'hosts.json'):
        data = read_json(p, None)
        if isinstance(data, list):
            return data
    return []


def is_me(h):
    return HOST in {str(h.get(k, '')).lower() for k in ('hostname', 'alias', 'label')}


def peers():
    return [h for h in hosts() if h.get('alias') and not is_me(h)]


def name(h):
    return (h.get('hostname') or h['alias']).lower()


def ssh_base(target_windows):
    args = [r'C:\Windows\System32\OpenSSH\ssh.exe' if WINDOWS else 'ssh', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=3', '-o', 'LogLevel=ERROR', '-o', 'Compression=yes']
    if not WINDOWS and not target_windows:  # reuse one connection; Windows' sshd is left alone
        run = os.environ.get('XDG_RUNTIME_DIR') or '/tmp'
        args += ['-o', 'ControlMaster=auto', '-o', f'ControlPath={run}/browser-handoff-%C', '-o', 'ControlPersist=600']
    return args


def send(h, data):
    """Copy our snapshot to one peer. Raises on failure."""
    target_windows = h.get('os') == 'windows'
    if target_windows:  # cmd.exe on the other side: write it with PowerShell from stdin
        dest = REMOTE_PEERS.replace('/', '\\')
        ps = (f"$d = Join-Path $HOME '{dest}'; New-Item -ItemType Directory -Force $d | Out-Null; "
              f"$t = Join-Path $d '.{HOST}.tmp'; [IO.File]::WriteAllText($t, [Console]::In.ReadToEnd()); "
              f"Move-Item -Force $t (Join-Path $d '{HOST}.json')")
        command = f'powershell -NoProfile -NonInteractive -Command "{ps}"'
    elif WINDOWS:  # Windows' ssh.exe never sends EOF on a piped stdin: copy a file with scp instead
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = STATE / f'.outgoing-{name(h)}.json'
        tmp.write_bytes(data)
        quiet = {'stdin': subprocess.DEVNULL, 'capture_output': True, 'timeout': 30}
        base = ssh_base(False)[1:]
        steps = [ssh_base(False) + [h['alias'], f'mkdir -p ~/{REMOTE_PEERS}'],
                 [r'C:\Windows\System32\OpenSSH\scp.exe', *base, str(tmp), f"{h['alias']}:{REMOTE_PEERS}/.{HOST}.tmp"],
                 ssh_base(False) + [h['alias'], f'mv ~/{REMOTE_PEERS}/.{HOST}.tmp ~/{REMOTE_PEERS}/{HOST}.json']]
        for step in steps:
            r = subprocess.run(step, **quiet)
            if r.returncode:
                break
        tmp.unlink(missing_ok=True)
    else:
        command = (f'd=~/{REMOTE_PEERS}; mkdir -p "$d" && cat > "$d/.{HOST}.tmp" && '
                   f'mv "$d/.{HOST}.tmp" "$d/{HOST}.json"')
    if not WINDOWS or target_windows:
        r = subprocess.run(ssh_base(target_windows) + [h['alias'], command], input=data,
                           capture_output=True, timeout=30)
    if r.returncode:
        raise OSError((r.stderr.decode(errors='replace').strip().splitlines() or [f'exit {r.returncode}'])[-1][:120])


_cache = {'mtime': None, 'snap': None}
_push_lock = threading.Lock()


def current(force=False):
    path, _ = session_file()
    if not path:
        return None
    mtime = path.stat().st_mtime
    if force or _cache['mtime'] != mtime or _cache['snap'] is None:
        _cache['snap'], _cache['mtime'] = snapshot(), mtime
    return _cache['snap']


def push(reason='push', force=False, quick=False):
    """Send our windows to every peer that doesn't have them yet. Returns a short report."""
    with _push_lock:
        snap = current(force)
        if not snap:
            return 'no Firefox profile here'
        snap = dict(snap, reason=reason, sent=time.time())
        dig = digest(snap)
        sent = read_json(STATE / 'sent.json', {})
        data = json.dumps(snap).encode()  # ASCII: survives cmd.exe/PowerShell pipes
        report, now = [], time.time()
        for h in peers():
            n = name(h)
            last = sent.get(n, {})
            if not force and last.get('digest') == dig:
                continue
            if not force and reason != 'sleep':
                if now - last.get('failed', 0) < BACKOFF:
                    continue
                if h.get('os') == 'windows' and now - last.get('time', 0) < WINDOWS_EVERY:
                    continue
            if quick and last.get('failed') and now - last['failed'] < BACKOFF:
                continue  # going to sleep: no time for a peer that's away
            try:
                send(h, data)
                sent[n] = {'digest': dig, 'time': now}
                report.append(f'{n}: sent')
            except subprocess.TimeoutExpired:
                sent[n] = dict(last, failed=now)
                report.append(f'{n}: skipped (timed out)')
            except OSError as e:
                sent[n] = dict(last, failed=now)
                report.append(f'{n}: skipped ({e})')
        write_json(STATE / 'sent.json', sent)
        write_json(STATE / 'snapshot.json', snap)
        tabs = sum(len(w['tabs']) for w in snap['windows'])
        return f"{plural(len(snap['windows']), 'window')}, {plural(tabs, 'tab')}; " + ('; '.join(report) or 'everyone has them')


# ---------- other machines' windows ----------
def received():
    """[(host, snapshot)], newest first."""
    out = []
    if PEERS.is_dir():
        for f in PEERS.glob('*.json'):
            snap = read_json(f, None)
            if isinstance(snap, dict) and snap.get('windows') and snap.get('host', f.stem) != HOST:
                out.append((snap.get('host', f.stem), snap))
    return sorted(out, key=lambda x: -x[1].get('saved', 0))


def ago(t):
    s = max(0, time.time() - t)
    if s < 90:
        return 'just now'
    if s < 3600:
        return f'{int(s // 60)} min ago'
    if s < 86400 * 2:
        return f'{int(s // 3600)} h ago'
    return f'{int(s // 86400)} days ago'


def current_title(tab):
    e = tab['entries'][tab['index'] - 1]
    return e.get('title') or e['url']


def plural(n, word):
    return f'{n} {word}' + ('' if n == 1 else 's')


def describe(host, snap):
    tabs = sum(len(w['tabs']) for w in snap['windows'])
    parts = [plural(tabs, 'tab')]
    if len(snap['windows']) > 1:
        parts.append(plural(len(snap['windows']), 'window'))
    parts.append(ago(snap.get('saved', 0)))
    return ' · '.join(parts)


# ---------- restoring ----------
def firefox_dirs():
    if WINDOWS:
        return [Path(os.environ.get(v, d)) / 'Mozilla Firefox'
                for v, d in (('ProgramFiles', r'C:\Program Files'), ('ProgramFiles(x86)', r'C:\Program Files (x86)'))]
    dirs = []
    exe = shutil.which('firefox')
    if exe:
        real = Path(exe).resolve()
        if real.suffix == '' and real.parent.name != 'bin':
            dirs.append(real.parent)
    return dirs + [Path('/usr/lib/firefox'), Path('/usr/lib64/firefox'), Path('/opt/firefox')]


def firefox_exe():
    if WINDOWS:
        for d in firefox_dirs():
            if (d / 'firefox.exe').exists():
                return str(d / 'firefox.exe')
        return 'firefox.exe'
    return shutil.which('firefox') or 'firefox'


def hook_dir():
    for d in firefox_dirs():
        if (d / 'browser-handoff.cfg').exists() and (d / 'defaults' / 'pref' / 'browser-handoff.js').exists():
            return d
    return None


def launch(args):
    kw = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if WINDOWS:
        kw['creationflags'] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True  # if Firefox wasn't running, this becomes Firefox
    subprocess.Popen([firefox_exe(), *args], **kw)


def open_urls(windows):
    """No hook: the same pages, without history or scroll."""
    for w in windows:
        urls = [t['entries'][t['index'] - 1]['url'] for t in w['tabs']]
        launch(['--new-window', urls[0]])
        time.sleep(1.0)
        for url in urls[1:]:
            launch(['--new-tab', url])
            time.sleep(0.15)


def notify(summary, body='', actions=None, wait=False):
    if WINDOWS or not shutil.which('notify-send'):
        return None
    cmd = ['notify-send', '-a', 'browser-handoff', '-i', 'firefox', summary, body]
    for key, label in (actions or {}).items():
        cmd.insert(1, f'--action={key}={label}')
    try:
        if not wait:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None
        return subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def resume(host=None, window=None):
    have = received()
    if host:
        have = [x for x in have if x[0] == host.lower()]
    if not have:
        msg = f'No windows from {host} yet.' if host else 'No windows from the other machines yet.'
        notify('Nothing to continue', msg)
        sys.exit(msg)
    host, snap = have[0]
    windows = snap['windows']
    if window:
        if not 1 <= window <= len(windows):
            sys.exit(f'{host} had {len(windows)} windows')
        windows = [windows[window - 1]]
    tabs = sum(len(w['tabs']) for w in windows)
    if not hook_dir():
        open_urls(windows)
        notify(f'Continued from {host}', f'{plural(tabs, "tab")} (scroll positions need the Firefox hook: '
               'browser-handoff install-firefox)')
        return
    STATE.mkdir(parents=True, exist_ok=True)
    for old in STATE.glob('restore-*'):
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)
    f = STATE / f'restore-{int(time.time() * 1000)}.json'
    write_json(f, {'windows': windows})
    launch(['--handoff-restore', str(f)])
    done, error = f.with_name(f.name + '.done'), f.with_name(f.name + '.error')
    for _ in range(60):  # Firefox may have to start first
        if done.exists():
            print(f'continued {plural(tabs, "tab")} from {host}')
            return
        if error.exists():
            reason = error.read_text(encoding='utf-8', errors='replace')[:200]
            open_urls(windows)
            notify(f'Continued from {host} without scroll positions', reason)
            sys.exit(f'Firefox hook failed ({reason}); opened the pages instead')
        time.sleep(0.5)
    notify(f'Continue from {host}', 'Firefox did not confirm the restore (still starting?)')


def pick():
    have = received()
    if not have:
        notify('Nothing to continue', 'No windows from the other machines yet.')
        return
    rows, actions = [], []
    for host, snap in have:
        rows.append(f'\U000f0239  Continue from {host}   ·  {describe(host, snap)}')
        actions.append((host, None))
        if len(snap['windows']) > 1:
            for i, w in enumerate(snap['windows'], 1):
                sel = w['tabs'][w['selected'] - 1]
                rows.append(f'      window {i} · {plural(len(w["tabs"]), "tab")} · {current_title(sel)[:60]}')
                actions.append((host, i))
    cmd = ['rofi', '-dmenu', '-i', '-no-custom', '-format', 'i', '-p', 'CONTINUE']
    if PICK_THEME.exists():
        cmd += ['-theme', str(PICK_THEME), '-theme-str',
                'window { width: 640px; } textbox-brand { content: "HANDOFF"; } '
                'textbox-hint { content: "ENTER  ·  TYPE TO FILTER  ·  ESC"; } listview { lines: 8; }']
    try:
        r = subprocess.run(cmd, input='\n'.join(rows), capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return
    out = r.stdout.strip()
    if r.returncode == 0 and out.isdigit():
        resume(*actions[int(out)])


# ---------- daemon ----------
class SleepWatch:
    """Holds a logind delay lock, so this machine waits (up to InhibitDelayMaxSec, 5 s by default)
    for one last send before it sleeps."""

    def __init__(self, on_sleep):
        self.on_sleep = on_sleep
        self.lock = None

    def take(self):
        if self.lock is None or self.lock.poll() is not None:
            self.lock = subprocess.Popen(['systemd-inhibit', '--what=sleep', '--mode=delay', '--who=browser-handoff',
                                          '--why=Sending open tabs to the other machines', 'sleep', 'infinity'],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def release(self):
        if self.lock and self.lock.poll() is None:
            self.lock.terminate()
            self.lock.wait(timeout=5)
        self.lock = None

    def run(self):
        if not shutil.which('gdbus') or not shutil.which('systemd-inhibit'):
            log('no gdbus/systemd-inhibit: no send before sleep')
            return
        while True:
            self.take()
            mon = subprocess.Popen(['gdbus', 'monitor', '--system', '--dest', 'org.freedesktop.login1',
                                    '--object-path', '/org/freedesktop/login1'], stdout=subprocess.PIPE, text=True)
            for line in mon.stdout:
                if 'PrepareForSleep' not in line:
                    continue
                if '(true,)' in line:
                    try:
                        log('sleep: ' + self.on_sleep())
                    except Exception as e:  # never keep the machine awake over this
                        log(f'sleep send failed: {e}')
                    self.release()
                elif '(false,)' in line:
                    log('woke up')
                    self.take()
            mon.wait()
            time.sleep(5)


def watch_peers(seen):
    """Offer to continue when another machine just went to sleep with windows open."""
    for host, snap in received():
        if snap.get('reason') != 'sleep' or snap.get('sent', 0) <= seen.get(host, 0):
            continue
        seen[host] = snap['sent']
        if time.time() - snap['sent'] > 600:
            continue

        def ask(host=host, snap=snap):
            choice = notify(f'{host} went to sleep', f'{describe(host, snap)}. Continue here?',
                            actions={'continue': 'Continue here'}, wait=True)
            if choice == 'continue':
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'continue', host])
        threading.Thread(target=ask, daemon=True).start()


def watch():
    log(f'watching Firefox on {HOST}; peers: ' + ', '.join(name(h) for h in peers()))
    threading.Thread(target=SleepWatch(lambda: push('sleep', quick=True)).run, daemon=True).start()
    seen = {host: snap.get('sent', 0) for host, snap in received()}
    last_mtime, last_push, last_peer_look = None, 0.0, 0.0
    while True:
        try:
            path, _ = session_file()
            mtime = path.stat().st_mtime if path else None
            now = time.time()
            if mtime and (mtime != last_mtime or now - last_push > BACKOFF) and now - last_push >= PUSH_EVERY:
                result = push('watch')
                if 'sent' in result or 'skipped' in result:
                    log(result)
                last_mtime, last_push = mtime, now
            if now - last_peer_look >= POLL:
                watch_peers(seen)
                last_peer_look = now
        except Exception as e:
            log(f'error: {e}')
        time.sleep(POLL)


# ---------- setup ----------
def install_daemon(log_fn=log):
    """Linux: link the command and run the daemon as a systemd user service (idempotent)."""
    if WINDOWS or not shutil.which('systemctl'):
        return
    me = Path(__file__).resolve()
    if not os.access(me, os.X_OK):
        me.chmod(0o755)
    link = HOME / '.local' / 'bin' / 'browser-handoff'
    if not link.is_symlink() or link.resolve() != me:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(me)
    unit = HOME / '.config' / 'systemd' / 'user' / UNIT
    text = ('[Unit]\nDescription=browser-handoff: keep the other machines up to date with the open Firefox windows\n'
            'After=network-online.target\n\n[Service]\n'
            f'ExecStart=/usr/bin/python3 {me} watch\nRestart=always\nRestartSec=15\nNice=10\n\n'
            '[Install]\nWantedBy=default.target\n')
    if unit.exists() and unit.read_text() == text:
        return
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(text)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True)
    subprocess.run(['systemctl', '--user', 'enable', UNIT], capture_output=True)
    subprocess.run(['systemctl', '--user', 'restart', UNIT], capture_output=True)
    log_fn(f'browser-handoff: installed {UNIT}')


def mesh_sync(log_fn=log):
    """Called by every `agent-mesh sync`: install the daemon here and send what changed."""
    install_daemon(log_fn)
    try:
        result = push('sync')
        if 'sent' in result:
            log_fn(f'browser-handoff: {result}')
    except Exception as e:
        log_fn(f'browser-handoff failed (mesh sync completed): {e}')


def install_firefox():
    src = MESH / 'firefox'
    if WINDOWS:
        d = firefox_dirs()[0]
        print('In an admin PowerShell:\n'
              f'  Copy-Item "{src / "browser-handoff.cfg"}" "{d}\\"\n'
              f'  Copy-Item "{src / "autoconfig.js"}" "{d}\\defaults\\pref\\browser-handoff.js"')
    else:
        d = next((x for x in firefox_dirs() if (x / 'omni.ja').exists()), Path('/usr/lib/firefox'))
        print(f'sudo install -m644 {src / "browser-handoff.cfg"} {d}/browser-handoff.cfg && '
              f'sudo install -m644 {src / "autoconfig.js"} {d}/defaults/pref/browser-handoff.js')
    print('Then restart Firefox once. The hook only loads ~/.agent-mesh/firefox/handoff.sys.mjs, so later '
          'changes arrive with the mesh.')


def status():
    path, profile = session_file()
    print('profile:', profile or 'none', '(open)' if profile and firefox_open(profile) else '')
    print('Firefox hook:', hook_dir() or 'not installed (browser-handoff install-firefox)')
    if not WINDOWS and shutil.which('systemctl'):
        r = subprocess.run(['systemctl', '--user', 'is-active', UNIT], capture_output=True, text=True)
        print('daemon:', r.stdout.strip())
    for n, s in sorted(read_json(STATE / 'sent.json', {}).items()):
        line = f'sent to {n}: ' + (ago(s['time']) if s.get('time') else 'never')
        if s.get('failed', 0) > s.get('time', 0):
            line += f' (last try failed {ago(s["failed"])})'
        print(line)
    for host, snap in received():
        print(f'from {host}: {describe(host, snap)}')


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else 'help'
    if cmd in ('help', '-h', '--help'):
        print(__doc__.strip())
    elif cmd == 'pick':
        pick()
    elif cmd == 'continue':
        rest = [a for a in args[1:] if not a.startswith('--')]
        window = None
        if '--window' in args:
            i = args.index('--window')
            window = int(args[i + 1])
            rest = [a for a in rest if a != args[i + 1]]
        resume(rest[0] if rest else None, window)
    elif cmd == 'list':
        have = received()
        if not have:
            print('nothing from the other machines yet')
        for host, snap in have:
            print(f'{host}: {describe(host, snap)}' + ('' if snap.get('open', True) else ' (Firefox was closed)'))
            for i, w in enumerate(snap['windows'], 1):
                for j, t in enumerate(w['tabs'], 1):
                    mark = '>' if j == w['selected'] else ' '
                    print(f'  {i}.{j}{mark} {current_title(t)[:90]}')
    elif cmd == 'push':
        print(push('push', force='--force' in args))
    elif cmd == 'snapshot':
        json.dump(snapshot(), sys.stdout, ensure_ascii=False)
    elif cmd == 'watch':
        watch()
    elif cmd == 'status':
        status()
    elif cmd == 'install-firefox':
        install_firefox()
    elif cmd == 'install':
        install_daemon()
    else:
        sys.exit('browser-handoff: unknown command (try browser-handoff help)')


if __name__ == '__main__':
    main()
