"""Share Claude Code and Codex chat sessions between machines, so `claude --resume` and `codex resume`
on one machine also list the sessions you ran on the others.

Each machine pulls from its peers (same OS only: a session remembers its working folder, and a
Windows path means nothing on Linux). Session files are append-only JSONL, so a file is copied when
it is missing here, and grown when the peer's copy is longer and starts with exactly what is here.
A session continued on two machines at once has diverged: it is left alone on both and logged.
Only whole lines are copied, so a session that is being written right now is never cut mid-line.

Peers come from hosts.json (the same list `cost` and Archfleet use). Linux peers only need ssh,
tar and python3. Run over SSH as `python3 - --manifest` to list this machine's session files.
"""
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HOME = Path.home()
WINDOWS = sys.platform == 'win32'
# (folder under the home, what to skip inside it)
ROOTS = (('.claude/projects', {'memory'}), ('.codex/sessions', set()))  # memory is shared by the mesh itself
CODEX_INDEX = '.codex/session_index.jsonl'  # thread names, one JSON line per rename
MAX_FILE = 200 * 1024 * 1024
PAUSE = 120  # seconds between automatic pulls (the hooks sync after every reply)
# Byte ranges of each file that came from another machine, so `cost`, `token-audit` and the ChatGPT
# export count a session once, on the machine that ran it: {"<path under home>": [[start, end], ...]}
LEDGER = Path('.local') / 'state' / 'agent-mesh' / 'foreign-sessions.json'


def ledger(home=HOME):
    try:
        return json.loads((home / LEDGER).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_ledger(data, home=HOME):
    write_new(home / LEDGER, json.dumps(data, sort_keys=True).encode(), None)


def is_link(p):
    return p.is_symlink() or getattr(p, 'is_junction', lambda: False)()  # a junction on Windows


def manifest(home=HOME):
    """{relative path: [size, mtime]} of every session file under ROOTS."""
    out = {}
    for root, skip in ROOTS:
        base = home / root
        if not base.is_dir():
            continue
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith('.') and not is_link(Path(dirpath, d))]
            for name in files:
                p = Path(dirpath, name)
                if name.startswith('.') or is_link(p):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size <= MAX_FILE:
                    out[p.relative_to(home).as_posix()] = [st.st_size, int(st.st_mtime)]
    index = home / CODEX_INDEX
    if index.is_file():
        out[CODEX_INDEX] = [index.stat().st_size, int(index.stat().st_mtime)]
    return out


def plan(local, remote):
    """Which of the peer's files might hold something new: missing here, or longer there."""
    return sorted(rel for rel, (size, _) in remote.items()
                  if rel not in local or size > local[rel][0] or rel == CODEX_INDEX and size != local[rel][0])


def whole_lines(data):
    cut = data.rfind(b'\n')
    return data[:cut + 1] if cut >= 0 else b''


def merge_index(here, there):
    """Union of the two thread-name logs, oldest first (Codex reads the last entry per thread)."""
    seen, rows = set(), []
    for line in (here + there).splitlines():
        if line.strip() and line not in seen:
            seen.add(line)
            try:
                when = json.loads(line).get('updated_at') or ''
            except ValueError:
                when = ''
            rows.append((when, line))
    rows.sort(key=lambda r: r[0])
    return b''.join(line + b'\n' for _, line in rows)


def apply(rel, data, mtime, home=HOME, foreign=None):
    """Bring one file up to the peer's copy. Returns 'new', 'grown', 'same' or 'diverged'.
    The bytes it adds are noted in `foreign` (the ledger)."""
    path = home / rel
    foreign = {} if foreign is None else foreign
    if rel == CODEX_INDEX:
        old = path.read_bytes() if path.exists() else b''
        merged = merge_index(old, data)
        if merged == old:
            return 'same'
        write_new(path, merged, None)
        return 'grown'
    data = whole_lines(data)
    if not data:
        return 'same'
    if not path.exists():
        write_new(path, data, mtime)
        foreign[rel] = [[0, len(data)]]
        return 'new'
    size = path.stat().st_size
    if size >= len(data):
        return 'same' if path.read_bytes().startswith(data) else 'diverged'
    with path.open('rb') as f:
        if f.read() != data[:size]:
            return 'diverged'
    with path.open('ab') as f:  # append, so a reader with the file open keeps the same file
        if f.tell() != size:
            return 'diverged'  # written to since we read it
        f.write(data[size:])
    ranges = foreign.setdefault(rel, [])
    if ranges and ranges[-1][1] == size:
        ranges[-1][1] = len(data)
    else:
        ranges.append([size, len(data)])
    os.utime(path, (mtime, mtime))
    return 'grown'


def write_new(path, data, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.agent-mesh-')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    if mtime:
        os.utime(tmp, (mtime, mtime))
    os.replace(tmp, path)


# ---------- talking to peers ----------
def hosts(mesh):
    for p in (mesh / 'hosts.json', HOME / '.config' / 'cost' / 'hosts.json', HOME / '.config' / 'archfleet' / 'hosts.json'):
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding='utf-8'))
            except ValueError:
                pass
    return []


def is_me(h):
    me = socket.gethostname().split('.')[0].lower()
    return me in {str(h.get(k, '')).lower() for k in ('hostname', 'alias', 'label')}


def peers(mesh):
    mine = 'windows' if WINDOWS else 'linux'
    return [h for h in hosts(mesh) if not is_me(h) and h.get('os', 'linux') == mine and h.get('alias')]


def ssh(alias, command, data=None, timeout=300):
    args = [r'C:\Windows\System32\OpenSSH\ssh.exe' if WINDOWS else 'ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=4', '-o', 'LogLevel=ERROR', alias, command]
    return subprocess.run(args, input=data, capture_output=True, timeout=timeout)


def pull(h, log):
    name = h.get('hostname') or h['alias']
    r = ssh(h['alias'], 'python3 - --manifest', Path(__file__).read_bytes(), timeout=60)
    try:
        remote = json.loads(r.stdout)
    except ValueError:
        err = (r.stderr.decode(errors='replace').strip().splitlines() or ['no answer'])[-1]
        return f'{name}: skipped ({err[:80]})'
    wanted = plan(manifest(), remote)
    foreign = ledger()
    counts = {'new': 0, 'grown': 0, 'diverged': 0}
    for start in range(0, len(wanted), 500):
        batch = wanted[start:start + 500]
        r = ssh(h['alias'], 'cd && tar -cf - --null -T - 2>/dev/null', b'\0'.join(p.encode() for p in batch))
        with tempfile.TemporaryFile() as buf:
            buf.write(r.stdout)
            buf.seek(0)
            try:
                tar = tarfile.open(fileobj=buf, mode='r:')
            except tarfile.TarError:
                return f'{name}: transfer failed'
            for member in tar:
                rel = member.name
                if not member.isfile() or rel not in remote or rel.startswith('/') or '..' in rel.split('/'):
                    continue
                result = apply(rel, tar.extractfile(member).read(), member.mtime, foreign=foreign)
                if result in counts:
                    counts[result] += 1
                if result == 'diverged':
                    log(f'sessions: {rel} differs on {name} (continued on both machines); left as is')
        save_ledger(foreign)
    return f"{name}: {counts['new']} new, {counts['grown']} grown" + (f", {counts['diverged']} diverged" if counts['diverged'] else '')


def share(mesh, log, force=False):
    """Pull every peer's sessions. Automatic runs wait PAUSE seconds between pulls."""
    stamp = mesh / '.git' / 'sessions-pulled'
    if not force and stamp.exists() and time.time() - stamp.stat().st_mtime < PAUSE:
        return None
    stamp.touch()
    results = []
    for h in peers(mesh):
        try:
            results.append(pull(h, log))
        except (subprocess.TimeoutExpired, OSError) as e:
            results.append(f"{h.get('hostname') or h['alias']}: skipped ({type(e).__name__})")
    return '; '.join(results) or 'no peers with the same OS in hosts.json'


if __name__ == '__main__' and '--manifest' in sys.argv:
    json.dump(manifest(), sys.stdout)
