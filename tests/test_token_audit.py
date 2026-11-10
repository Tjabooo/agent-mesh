import importlib.machinery
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('token_audit', str(Path(__file__).parents[1] / 'bin/token-audit'))
spec = importlib.util.spec_from_loader('token_audit', loader)
ta = importlib.util.module_from_spec(spec)
loader.exec_module(ta)


def at(minutes):
    t = datetime.now(timezone.utc) - timedelta(hours=3) + timedelta(minutes=minutes)
    return t.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def reply(i, minutes, cr, cw, tool=None):
    content = [{'type': 'text', 'text': 'ok'}]
    if tool:
        content.append({'type': 'tool_use', 'id': f'tu{i}', 'name': tool[0], 'input': tool[1]})
    return {'type': 'assistant', 'timestamp': at(minutes), 'requestRef': f'r{i}', 'promptCacheRequested': '1h',
            'message': {'id': f'm{i}', 'model': 'claude-opus-5-5', 'content': content,
                        'usage': {'input_tokens': 2, 'output_tokens': 50, 'cache_read_input_tokens': cr,
                                  'cache_creation_input_tokens': cw}}}


def result(i, minutes, text):
    return {'type': 'user', 'timestamp': at(minutes),
            'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': f'tu{i}', 'content': text}]}}


class TokenAuditTest(unittest.TestCase):
    def test_scan_reports_usage_big_results_rereads_and_cache_breaks(self):
        shape = {'system': [{'type': 'text', 'text': 'x' * 4000}],
                 'tools': [{'name': 'Bash', 'description': 'y' * 2000}, {'name': 'Read', 'description': 'z' * 1000}]}
        recs = [
            {'type': 'api-request-shape', 'shapeHash': 'h1', 'shape': shape, 'timestamp': at(0)},
            {'type': 'api-request-blob', 'hash': 'b1', 'message': {'role': 'user', 'content': [
                {'type': 'text', 'text': 'Contents of /x/MEMORY.md (memory):\n' + 'm' * 3000}]}},
            {'type': 'api-request', 'id': 'r1', 'shapeHash': 'h1', 'tail': ['b1'], 'timestamp': at(0)},
            reply(1, 0, 0, 3000, ('Bash', {'command': 'cat big.log'})),
            result(1, 1, 'L' * 30000),
            reply(2, 2, 3000, 8000, ('Read', {'file_path': '/src/app.js'})),
            result(2, 3, 'A' * 5000),
            reply(3, 4, 11000, 1500, ('Read', {'file_path': '/src/app.js'})),
            result(3, 5, 'A' * 5000),
            reply(4, 6, 12500, 100),
            reply(5, 140, 0, 12600),  # back after more than the 1 h TTL: everything re-written
            {'type': 'ai-title', 'aiTitle': 'Test session'},
        ]
        with tempfile.TemporaryDirectory() as home:
            p = Path(home) / '.claude' / 'projects' / 'proj' / 'sess1.jsonl'
            p.parent.mkdir(parents=True)
            p.write_text(''.join(json.dumps(r) + '\n' for r in recs), encoding='utf-8')
            with patch.object(Path, 'home', return_value=Path(home)):
                out = ta.scan(14)
        self.assertEqual(out['sessions'], 1)
        u = out['usage']['proj\tclaude-opus-5-5\tmain']
        self.assertEqual(u['n'], 5)
        self.assertEqual(u['cr'], 3000 + 11000 + 12500)
        self.assertEqual(out['unfiltered_total'][0], 1)       # cat with 30k chars of output
        self.assertEqual(out['reread_total'][0], 1)           # app.js read twice, no edit between
        self.assertIn('gap>60m TTL', out['breaks'])
        self.assertEqual(out['breaks']['gap>60m TTL'][0], 1)
        parts = dict(out['latest']['parts'])
        self.assertIn('file: /x/MEMORY.md', parts)
        self.assertIn('tool schema: Bash', parts)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ta.report({'test': out}, {}, 14, 10)
        self.assertIn('Cache breaks', buf.getvalue())
        self.assertIn('cat big.log', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
