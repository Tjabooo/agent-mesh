import argparse
import importlib.machinery
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('cost', str(Path(__file__).parents[1] / 'bin/cost'))
spec = importlib.util.spec_from_loader('cost', loader)
cost = importlib.util.module_from_spec(spec)
loader.exec_module(cost)

TODAY = date.today().isoformat() + 'T12:00:00Z'


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')


def claude_msg(msg_id, model='claude-opus-5-5', out=100):
    return {'type': 'assistant', 'timestamp': TODAY, 'requestId': 'req_' + msg_id,
            'message': {'id': msg_id, 'model': model, 'usage': {
                'input_tokens': 10, 'output_tokens': out, 'cache_read_input_tokens': 1000,
                'cache_creation_input_tokens': 500,
                'cache_creation': {'ephemeral_1h_input_tokens': 500, 'ephemeral_5m_input_tokens': 0}}}}


class PriceTests(unittest.TestCase):
    def test_matches_claude_codes_own_cost(self):
        # Usage and costUSD as Claude Code recorded them for one subagent run each
        opus = {'input_tokens': 4, 'output_tokens': 646, 'cache_read_input_tokens': 34889,
                'cache_creation': {'ephemeral_1h_input_tokens': 13984, 'ephemeral_5m_input_tokens': 0}}
        self.assertAlmostEqual(cost.claude_price('claude-opus-5-5', opus, 'standard')[0], 0.1317858, places=7)
        haiku = {'input_tokens': 1187, 'output_tokens': 19}
        self.assertAlmostEqual(cost.claude_price('claude-haiku-5-5', haiku, None)[0], 0.0001282, places=7)

    def test_fast_mode_and_unknown_model(self):
        u = {'input_tokens': 1_000_000}
        self.assertEqual(cost.claude_price('claude-opus-5-5', u, 'fast')[0], 8)
        self.assertIsNone(cost.claude_price('claude-unknown-9', u, None)[0])

    def test_codex_long_context(self):
        short = {'input_tokens': 200_000, 'cached_input_tokens': 100_000, 'output_tokens': 1000}
        self.assertAlmostEqual(cost.codex_price('gpt-6.1-sol', short, 'default')[0], 0.2 + 0.01 + 0.01)
        long = dict(short, input_tokens=300_000)
        self.assertAlmostEqual(cost.codex_price('gpt-6.1-sol', long, 'default')[0], 0.8 + 0.02 + 0.015)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        patcher = patch.object(Path, 'home', return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_claude_dedupes_and_counts_subagents(self):
        project = self.home / '.claude/projects/p'
        a, b = claude_msg('msg_a'), claude_msg('msg_b', model='claude-sonnet-5-5')
        write_jsonl(project / 's1.jsonl', [a, a, b, {'type': 'ai-title', 'aiTitle': 'Fix it', 'sessionId': 's1'}])
        write_jsonl(project / 's2.jsonl', [a])  # a forked transcript repeats msg_a
        write_jsonl(project / 's1/subagents/agent-1.jsonl', [claude_msg('msg_c', model='claude-haiku-5-5')])
        write_jsonl(project / 's3.jsonl', [dict(claude_msg('msg_d'), message=dict(claude_msg('msg_d')['message'], model='<synthetic>'))])
        rows = cost.scan()
        self.assertEqual(sum(r['calls'] for r in rows), 3)
        self.assertEqual({r['session'] for r in rows}, {'s1'})
        self.assertEqual({r['title'] for r in rows}, {'Fix it'})

    def test_codex_uses_turn_models(self):
        write_jsonl(self.home / '.codex/session_index.jsonl',
                    [{'id': 'th1', 'thread_name': 'Codex job'}])
        usage = {'input_tokens': 1000, 'cached_input_tokens': 600, 'output_tokens': 50, 'reasoning_output_tokens': 10}
        write_jsonl(self.home / '.codex/sessions/2026/01/01/rollout-x.jsonl', [
            {'type': 'session_meta', 'payload': {'id': 'th1'}},
            {'type': 'turn_context', 'payload': {'turn_id': 't1', 'model': 'gpt-6-astra'}},
            {'type': 'token_usage_record', 'timestamp': TODAY, 'payload': {'turn_id': 't1', 'response_id': 'r1', 'session_id': 'th1', 'usage': usage}},
            {'type': 'turn_context', 'payload': {'turn_id': 't2', 'model': 'gpt-6.1-sol'}},
            {'type': 'token_usage_record', 'timestamp': TODAY, 'payload': {'turn_id': 't2', 'response_id': 'r2', 'session_id': 'th1', 'usage': usage}},
            {'type': 'token_usage_record', 'timestamp': TODAY, 'payload': {'turn_id': 't2', 'response_id': 'r2', 'session_id': 'th1', 'usage': usage}},
            {'type': 'event_msg', 'timestamp': TODAY, 'payload': {'type': 'token_count', 'info': {'last_token_usage': usage, 'total_token_usage': usage}}},
        ])
        rows = cost.scan()
        self.assertEqual({r['model']: r['calls'] for r in rows}, {'gpt-6-astra': 1, 'gpt-6.1-sol': 1})
        astra = next(r for r in rows if r['model'] == 'gpt-6-astra')
        self.assertEqual((astra['input'], astra['cache_read'], astra['title']), (400, 600, 'Codex job'))

    def test_report(self):
        write_jsonl(self.home / '.claude/projects/p/s1.jsonl', [claude_msg('msg_a')])
        args = argparse.Namespace(days=None, since=None, local=True, daily=False, top=3, json=False)
        out = io.StringIO()
        with redirect_stdout(out):
            cost.report({'here': cost.scan()}, {'there': 'unreachable (asleep?)'}, args)
        self.assertIn('Total', out.getvalue())
        self.assertIn('claude-opus-5-5', out.getvalue())
        self.assertIn('there: unreachable', out.getvalue())


if __name__ == '__main__':
    unittest.main()
