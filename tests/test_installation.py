"""Sandboxed user installation; no packages or real applications are launched."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gdw install space $ ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        for tool in ('code', 'konsole', 'ss', 'xdg-open'):
            self.script(tool, '#!/bin/sh\nexit 0\n')
        self.script('quickshell', '#!/bin/sh\nif [ "$1" = --version ]; then echo "Quickshell 0.3.2"; else echo true; fi\n')
        self.env = dict(os.environ, XDG_CONFIG_HOME=str(self.root / 'config'), XDG_DATA_HOME=str(self.root / 'data'), XDG_BIN_HOME=str(self.root / 'bin'), XDG_RUNTIME_DIR=str(self.root / 'run'), PATH=str(self.tools) + os.pathsep + os.defpath)
        Path(self.env['XDG_RUNTIME_DIR']).mkdir()
        self.config = Path(self.env['XDG_CONFIG_HOME']) / 'garuda-developer-workspace/config.json'
        self.launcher = Path(self.env['XDG_BIN_HOME']) / 'garuda-developer-workspace'

    def script(self, name, body):
        target = self.tools / name
        target.write_text(body)
        target.chmod(0o755)
        return target

    def run_command(self, *args):
        return subprocess.run(args, cwd=REPO, env=self.env, capture_output=True, text=True, timeout=15)

    def install(self):
        result = self.run_command('bash', 'install.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_install_update_launcher_and_uninstall_preserve_config(self):
        self.install()
        self.assertEqual(json.loads(self.config.read_text())['projectRoots'], ['~/Projects'])
        custom = '{"projectRoots": ["~/work space"], "maxDepth": 2}\n'
        self.config.write_text(custom)
        self.install()
        self.assertEqual(self.config.read_text(), custom)
        self.assertTrue(self.launcher.is_file())
        self.assertEqual(self.run_command(str(self.launcher), 'services').returncode, 0)
        desktop = Path(self.env['XDG_DATA_HOME']) / 'applications/garuda-developer-workspace.desktop'
        if shutil.which('desktop-file-validate'):
            self.assertEqual(self.run_command('desktop-file-validate', str(desktop)).returncode, 0)
        self.assertEqual(self.run_command('bash', 'uninstall.sh').returncode, 0)
        self.assertFalse(self.launcher.exists())
        self.assertEqual(self.config.read_text(), custom)
        self.assertEqual(self.run_command('bash', 'uninstall.sh', '--purge-config').returncode, 0)
        self.assertFalse(self.config.exists())

    def test_dependency_failure_leaves_existing_settings_and_code_untouched(self):
        self.install()
        original = self.launcher.read_bytes()
        self.script('quickshell', '#!/bin/sh\necho "Quickshell 0.2.1"\n')
        result = self.run_command('bash', 'install.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('0.3.x is required', result.stderr)
        self.assertEqual(self.launcher.read_bytes(), original)
        self.assertTrue(self.config.exists())

    def test_launcher_retries_a_not_ready_ipc_reply(self):
        self.install()
        count = self.root / 'calls'
        self.env['GDW_TEST_CALLS'] = str(count)
        self.script('quickshell', '''#!/usr/bin/env python3
import os, pathlib, sys
counter = pathlib.Path(os.environ['GDW_TEST_CALLS'])
if sys.argv[1] == '--version':
    print('Quickshell 0.3.2')
elif sys.argv[1] == 'ipc':
    count = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(count + 1))
    print('Not ready to accept queries yet.' if count < 2 else 'true')
''')
        result = self.run_command(str(self.launcher))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count.read_text(), '3')

    def test_custom_configured_editor_and_terminal_are_checked(self):
        self.config.parent.mkdir(parents=True)
        editor = self.script('custom editor', '#!/bin/sh\nexit 0\n')
        terminal = self.script('custom terminal', '#!/bin/sh\nexit 0\n')
        text = json.dumps({'editor': {'executable': str(editor), 'arguments': []}, 'terminal': {'executable': str(terminal), 'arguments': []}})
        self.config.write_text(text)
        self.install()
        self.assertEqual(self.config.read_text(), text)
        editor.unlink()
        result = self.run_command('bash', 'install.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not found', result.stderr)
        self.assertEqual(self.config.read_text(), text)

    def test_launcher_rejects_unknown_modes(self):
        self.install()
        self.assertEqual(self.run_command(str(self.launcher), '--bad').returncode, 2)


if __name__ == '__main__':
    unittest.main()
