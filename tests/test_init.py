import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).parents[1] / 'bin' / 'agent-mesh'


class InitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.mesh = self.home / '.agent-mesh'
        self.home.mkdir()
        env = {'HOME': str(self.home), 'USERPROFILE': str(self.home), 'AGENT_MESH_DIR': str(self.mesh)}
        with patch.dict(os.environ, env):  # HOME and the mesh path are read when the tool loads
            loader = importlib.machinery.SourceFileLoader('agent_mesh_tool', str(TOOL))
            spec = importlib.util.spec_from_loader('agent_mesh_tool', loader)
            self.tool = importlib.util.module_from_spec(spec)
            loader.exec_module(self.tool)

    def test_init_copies_the_exporter_next_to_the_tool(self):
        with patch.object(self.tool, 'sync', return_value='in step'), patch.object(self.tool, 'link_command'), \
                patch('builtins.print'):
            self.tool.init([])
        self.assertTrue((self.mesh / 'bin' / 'agent-mesh').is_file())
        self.assertTrue((self.mesh / 'bin' / 'agent_mesh_export.py').is_file())

    def test_missing_exporter_is_logged_not_raised(self):
        self.mesh.mkdir(parents=True)
        (self.mesh / 'chatgpt.json').write_text('{"enabled": true}')
        logged = []
        with patch.object(self.tool, 'log', logged.append), \
                patch.dict(sys.modules, {'agent_mesh_export': None}):  # None makes the import fail
            self.assertIs(self.tool.export_chatgpt(), False)
        self.assertIn('agent_mesh_export.py is missing', logged[0])


if __name__ == '__main__':
    unittest.main()
