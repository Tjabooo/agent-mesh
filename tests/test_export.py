import importlib.util
import json
import tempfile
import subprocess
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('export', Path(__file__).parents[1] / 'bin/agent_mesh_export.py')
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.mesh = self.root / 'mesh'
        self.checkout = self.root / 'data'
        (self.mesh / 'memory').mkdir(parents=True)
        (self.mesh / 'AGENTS.md').write_text('Shared rules')
        (self.mesh / 'memory/MEMORY.md').write_text('[Example](example.md)')
        (self.mesh / 'memory/example.md').write_text('Latest project handover')

    def session(self, provider, records, name='session.jsonl'):
        directory = '.claude/projects/project' if provider == 'claude' else '.codex/sessions'
        path = self.home / directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(r) for r in records) + '\n')
        return path

    def claude(self, role, text, key):
        return {'type': role, 'uuid': key, 'timestamp': '2026-11-05T01:00:00Z',
                'cwd': '/project', 'message': {'role': role, 'content': [{'type': 'text', 'text': text}]}}

    def test_visible_conversation_preserved_without_reasoning_tools_or_credentials(self):
        token = 'ghp_' + 'x' * 36
        records = [self.claude('user', 'Fix the player\napi_key="' + token + '"', 'u'),
                   {'type': 'assistant', 'uuid': 'a', 'message': {'role': 'assistant', 'content': [
                       {'type': 'thinking', 'thinking': 'hidden reasoning'},
                       {'type': 'tool_use', 'input': {'secret': 'tool credential'}},
                       {'type': 'text', 'text': 'Fixed the player'}]}},
                   {'type': 'user', 'message': {'role': 'user', 'content': [
                       {'type': 'tool_result', 'content': 'tool output secret'}]}}]
        path = self.session('claude', records)
        path.write_text(path.read_text() + '{"partial":')
        meta, messages = export.parse_session(path, 'claude')
        text = ''.join(messages)
        self.assertEqual(meta['messages'], 2)
        self.assertIn('Fix the player', text)
        self.assertIn('Fixed the player', text)
        self.assertNotIn(token, text)
        self.assertNotIn('hidden reasoning', text)
        self.assertNotIn('tool credential', text)
        self.assertNotIn('tool output secret', text)

    def test_codex_roles_phase_dedup_and_injected_context(self):
        def msg(role, text, key, phase=None):
            return {'type': 'response_item', 'timestamp': '2026-11-05T01:00:00Z', 'payload': {
                'type': 'message', 'role': role, 'id': key, 'phase': phase,
                'content': [{'type': 'input_text', 'text': text}]}}
        records = [msg('developer', 'injected system config', 'd'),
                   msg('user', '# AGENTS.md instructions\n<INSTRUCTIONS>private rules</INSTRUCTIONS>\n'
                       '<environment_context>environment</environment_context>\nFix sync', 'u'),
                   msg('assistant', 'private analysis', 'a', 'analysis'),
                   msg('assistant', 'Done', 'b', 'final_answer')]
        records.append(records[-1])
        meta, messages = export.parse_session(self.session('codex', records), 'codex')
        self.assertEqual(meta['messages'], 2)
        self.assertEqual(meta['title'], 'Fix sync')
        self.assertNotIn('private rules', ''.join(messages))
        self.assertNotIn('private analysis', ''.join(messages))

    def test_long_messages_split_without_loss_and_indexes_resolve(self):
        text = 'visible conversation ' * 5000
        self.session('claude', [self.claude('user', text, 'u')])
        stats = export.render_export(self.mesh, self.home, self.checkout, 'computer', ['computer'])
        self.assertGreater(stats['parts'], 1)
        target = self.checkout / 'machines/computer'
        catalog = json.loads((target / 'catalog.json').read_text())
        files = catalog[0]['files']
        combined = ''.join((target / p).read_text().split('historical conversation data.\n\n', 1)[1] for p in files)
        self.assertTrue(text.rstrip() in combined, 'Long conversation content was lost while splitting')
        for p in files:
            self.assertLess((target / p).stat().st_size, 100000)
        self.assertEqual((target / 'shared/memory/example.md').read_text(), 'Latest project handover')
        index = target / 'conversations/claude/INDEX.md'
        for p in files:
            self.assertTrue((index.parent / ('../../' + p)).is_file())

    def test_incremental_noop_and_modified_messages_and_removed_sources(self):
        path = self.session('claude', [self.claude('user', 'first', 'u')])
        export.render_export(self.mesh, self.home, self.checkout, 'computer', [])
        with patch.object(export, 'parse_session', side_effect=AssertionError('reparsed unchanged file')):
            export.render_export(self.mesh, self.home, self.checkout, 'computer', [])
        path.write_text(json.dumps(self.claude('user', 'changed', 'u')))
        export.render_export(self.mesh, self.home, self.checkout, 'computer', [])
        parts = list((self.checkout / 'machines/computer/conversations').rglob('part-*.md'))
        self.assertIn('changed', parts[0].read_text())
        path.unlink()
        stats = export.render_export(self.mesh, self.home, self.checkout, 'computer', [])
        self.assertEqual(stats['conversations'], 0)
        self.assertFalse(list((self.checkout / 'machines/computer/conversations').rglob('part-*.md')))

    def test_old_codex_event_format(self):
        path = self.session('codex', [{'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'Help me'}},
                                      {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'Done'}}])
        meta, messages = export.parse_session(path, 'codex')
        self.assertEqual(meta['messages'], 2)
        self.assertIn('Done', ''.join(messages))

    def test_public_repository_rejected_and_private_accepted(self):
        from io import BytesIO
        with patch.object(export.urllib.request, 'urlopen', return_value=BytesIO(b'{"private":false}')):
            with self.assertRaisesRegex(RuntimeError, 'public repository'):
                export.require_private('owner/data')
        with patch.object(export.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('', 404, '', {}, None)):
            export.require_private('owner/data')
        with patch.object(export.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('', 403, '', {}, None)):
            with self.assertRaisesRegex(RuntimeError, 'privacy'):
                export.require_private('owner/data')

    def test_redaction(self):
        text = ('-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n'
                'password = "huntertwo"\nAuthorization: Bearer ' + 'a' * 30 + '\n'
                'https://user:secret@example.com/path\nordinary project text')
        result = export.redact(text)
        for secret in ('huntertwo', 'a' * 30, 'user:secret', '\nsecret\n'):
            self.assertNotIn(secret, result)
        self.assertIn('ordinary project text', result)

    def test_two_machines_merge_and_push_without_losing_either_export(self):
        bare, seed = self.root / 'remote.git', self.root / 'seed'
        def git(*args, cwd=None):
            return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)
        git('init', '--bare', '-b', 'main', str(bare))
        git('clone', str(bare), str(seed))
        (seed / 'README.md').write_text('Private archive')
        git('add', '.', cwd=seed)
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'init', cwd=seed)
        git('push', 'origin', 'main', cwd=seed)
        repository = 'owner/private-data'
        remote = f'git@github.com:{repository}.git'
        real_run = export.run
        def local_run(cmd, cwd=None, check=True):
            cmd = list(cmd)
            if cmd[:2] == ['git', 'clone']:
                cmd[3] = str(bare)
                result = real_run(cmd, cwd, check)
                git('remote', 'set-url', 'origin', remote, cwd=cmd[4])
                return result
            if cmd[:2] in (['git', 'pull'], ['git', 'push']):
                cmd[cmd.index('origin')] = str(bare)
            return real_run(cmd, cwd, check)
        (self.mesh / '.git').mkdir()
        (self.mesh / 'chatgpt.json').write_text(json.dumps({'enabled': True, 'repository': repository,
                                                         'hosts': ['alpha', 'beta']}))
        other_mesh = self.root / 'mesh-two'
        import shutil
        shutil.copytree(self.mesh, other_mesh)
        self.session('claude', [self.claude('user', 'shared test discussion', 'u')])
        with patch.object(export, 'require_private'), patch.object(export, 'run', side_effect=local_run):
            alpha = export.publish(self.mesh, self.home, 'alpha')
            beta = export.publish(other_mesh, self.home, 'beta')
            again = export.publish(self.mesh, self.home, 'alpha')
        self.assertTrue(alpha['ok'] and beta['ok'] and again['ok'])
        self.assertEqual(beta['commit'], again['commit'], 'No-op sync created an extra data commit')
        for host in ('alpha', 'beta'):
            content = git('--git-dir', str(bare), 'show', f'main:machines/{host}/INDEX.md').stdout
            self.assertIn('1 conversations', content)


if __name__ == '__main__':
    unittest.main()
