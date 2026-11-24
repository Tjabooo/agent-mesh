import json
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).parents[1] / 'bin'
sys.path.insert(0, str(BIN))
import browser_handoff as b  # noqa: E402


def tab(*urls, index=None, **extra):
    entries = [{'url': u, 'title': u.upper(), 'cacheKey': 1, 'docshellUUID': 'x'} for u in urls]
    return dict({'entries': entries, 'index': index or len(entries)}, **extra)


class Lz4(unittest.TestCase):
    def test_literals_and_overlapping_match(self):
        # "abc", then a 9-byte match 3 back (overlaps itself), then the closing literal "x"
        block = bytes([0x35]) + b'abc' + bytes([3, 0]) + bytes([0x10]) + b'x'
        self.assertEqual(b.unlz4(block), b'abcabcabcabcx')

    def test_long_literal_and_match_lengths(self):
        lit = bytes(range(256)) * 2  # 512 literals: 15 + 255 + 242
        block = bytes([0xFF]) + bytes([255, 242]) + lit + bytes([0, 2]) + bytes([1]) + bytes([0x10]) + b'z'
        out = b.unlz4(block)  # match: 15 + 1 + 4 = 20 bytes copied from 512 back
        self.assertEqual(out, lit + lit[:20] + b'z')

    def test_mozlz4_file(self):
        with tempfile.TemporaryDirectory() as d:
            raw = json.dumps({'windows': []}).encode()
            f = Path(d) / 'recovery.jsonlz4'
            f.write_bytes(b'mozLz40\0' + len(raw).to_bytes(4, 'little') + bytes([0xF0, len(raw) - 15]) + raw)
            self.assertEqual(b.read_mozlz4(f), {'windows': []})


class Small(unittest.TestCase):
    def test_tab_keeps_history_scroll_and_form_but_not_storage(self):
        t = b.small_tab(tab('https://a', 'https://b', index=2, scroll={'scroll': '0,900'},
                            formdata={'id': {'q': 'hi'}}, storage={'token': 'secret'}, userContextId=3))
        self.assertEqual([e['url'] for e in t['entries']], ['https://a', 'https://b'])
        self.assertEqual(t['index'], 2)
        self.assertEqual(t['scroll'], {'scroll': '0,900'})
        self.assertEqual(t['formdata'], {'id': {'q': 'hi'}})
        self.assertNotIn('storage', t)
        self.assertNotIn('userContextId', t)
        self.assertNotIn('cacheKey', t['entries'][0])

    def test_long_history_is_cut_and_index_moves_with_it(self):
        urls = [f'https://p{i}' for i in range(60)]
        t = b.small_tab(tab(*urls, index=50))
        self.assertEqual(len(t['entries']), b.MAX_ENTRIES + 5)
        self.assertEqual(t['entries'][t['index'] - 1]['url'], 'https://p49')

    def test_blank_and_hidden_tabs_are_dropped(self):
        self.assertIsNone(b.small_tab(tab('about:newtab')))
        self.assertIsNone(b.small_tab(tab('https://a', hidden=True)))

    def test_window_selected_tab_follows_dropped_tabs(self):
        w = b.small_window({'tabs': [tab('about:newtab'), tab('https://a'), tab('https://b')], 'selected': 3})
        self.assertEqual(len(w['tabs']), 2)
        self.assertEqual(w['tabs'][w['selected'] - 1]['entries'][0]['url'], 'https://b')
        self.assertIsNone(b.small_window({'tabs': [tab('about:blank')], 'selected': 1}))

    def test_digest_ignores_timestamps(self):
        w = [{'tabs': [{'entries': [{'url': 'https://a'}], 'index': 1}], 'selected': 1}]
        self.assertEqual(b.digest({'windows': w, 'saved': 1}), b.digest({'windows': w, 'saved': 2}))


if __name__ == '__main__':
    unittest.main()
