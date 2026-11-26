import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).parents[1] / 'bin'
sys.path.insert(0, str(BIN))
import agent_mesh_sessions as s  # noqa: E402

REL = '.claude/projects/-home-me/a.jsonl'


class Apply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.foreign = {}

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, data, rel=REL):
        return s.apply(rel, data, 1000, self.home, self.foreign)

    def test_new_file_keeps_only_whole_lines(self):
        self.assertEqual(self.apply(b'{"a":1}\n{"b":2}\n{"part'), 'new')
        self.assertEqual((self.home / REL).read_bytes(), b'{"a":1}\n{"b":2}\n')
        self.assertEqual(self.foreign[REL], [[0, 16]])

    def test_grows_when_peer_copy_extends_ours(self):
        self.apply(b'{"a":1}\n')
        self.assertEqual(self.apply(b'{"a":1}\n{"b":2}\n'), 'grown')
        self.assertEqual((self.home / REL).read_bytes(), b'{"a":1}\n{"b":2}\n')
        self.assertEqual(self.foreign[REL], [[0, 16]])

    def test_local_growth_is_not_marked_foreign(self):
        path = self.home / REL
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{"mine":1}\n')
        self.assertEqual(self.apply(b'{"mine":1}\n{"theirs":2}\n'), 'grown')
        self.assertEqual(self.foreign[REL], [[11, 24]])

    def test_diverged_is_left_alone(self):
        self.apply(b'{"a":1}\n')
        with (self.home / REL).open('ab') as f:
            f.write(b'{"here":2}\n')
        self.assertEqual(self.apply(b'{"a":1}\n{"there":2}\n{"more":3}\n'), 'diverged')
        self.assertEqual((self.home / REL).read_bytes(), b'{"a":1}\n{"here":2}\n')

    def test_shorter_peer_copy_changes_nothing(self):
        self.apply(b'{"a":1}\n{"b":2}\n')
        self.assertEqual(self.apply(b'{"a":1}\n'), 'same')

    def test_codex_thread_names_merge(self):
        index = self.home / s.CODEX_INDEX
        index.parent.mkdir(parents=True)
        index.write_bytes(b'{"id":"1","thread_name":"x","updated_at":"2026-02"}\n')
        self.apply(b'{"id":"2","thread_name":"y","updated_at":"2026-01"}\n', rel=s.CODEX_INDEX)
        self.assertEqual(index.read_bytes().splitlines(),
                         [b'{"id":"2","thread_name":"y","updated_at":"2026-01"}',
                          b'{"id":"1","thread_name":"x","updated_at":"2026-02"}'])


class Plan(unittest.TestCase):
    def test_wants_missing_and_longer_files_only(self):
        local = {'a': [10, 0], 'b': [10, 0], 'c': [10, 0]}
        remote = {'a': [10, 0], 'b': [20, 0], 'c': [5, 0], 'd': [1, 0]}
        self.assertEqual(s.plan(local, remote), ['b', 'd'])

    def test_manifest_skips_memory_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / '.claude/projects/p/memory').mkdir(parents=True)
            (home / '.claude/projects/p/memory/MEMORY.md').write_text('x')
            (home / '.claude/projects/p/s.jsonl').write_text('{}\n')
            (home / '.codex/sessions/2026/01').mkdir(parents=True)
            (home / '.codex/sessions/2026/01/r.jsonl').write_text('{}\n')
            self.assertEqual(sorted(s.manifest(home)), ['.claude/projects/p/s.jsonl', '.codex/sessions/2026/01/r.jsonl'])


class Ledger(unittest.TestCase):
    def test_cost_skips_copied_bytes(self):
        loader = importlib.machinery.SourceFileLoader('cost_mod', str(BIN / 'cost'))
        spec = importlib.util.spec_from_loader('cost_mod', loader)
        cost = importlib.util.module_from_spec(spec)
        loader.exec_module(cost)
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            path = Path(tmp) / 'x.jsonl'
            path.write_bytes(b'{"n":1}\n{"n":2}\n{"n":3}\n')
            rel = path.resolve().relative_to(Path.home().resolve()).as_posix()
            cost.FOREIGN = {rel: [[8, 16]]}
            self.assertEqual([json.loads(line)['n'] for line in cost.own_lines(path)], [1, 3])


if __name__ == '__main__':
    unittest.main()
