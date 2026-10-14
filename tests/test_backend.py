import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('backend', Path(__file__).resolve().parents[1] / 'plugin/backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(backend.DEFAULTS)
        self.settings = patch.object(backend, 'settings', return_value=self.cfg)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.resolve = patch.object(backend, 'executable', side_effect=lambda s: '/usr/bin/' + s if '/' not in s else s)
        self.resolve.start()
        self.addCleanup(self.resolve.stop)

    def test_discovery_normalizes_roots_skips_dependencies_and_groups_monorepos(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / 'project with spaces;$(echo ignored)'
            (project / 'apps/web').mkdir(parents=True)
            (project / '.git').mkdir()
            (project / 'apps/web/package.json').touch()
            (root / 'node_modules/hidden').mkdir(parents=True)
            (root / 'node_modules/hidden/Cargo.toml').touch()
            alias = root / 'alias'
            alias.symlink_to(root, target_is_directory=True)
            self.cfg['projectRoots'] = [folder, str(alias), folder + '/.']
            self.assertEqual([p['path'] for p in backend.projects()], [str(project)])

    def test_depth_and_overlap_are_deliberate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'a/b/c').mkdir(parents=True)
            (root / 'a/b/c/go.mod').touch()
            self.cfg.update(projectRoots=[folder, str(root / 'a')], maxDepth=2)
            self.assertEqual(backend.projects(), [])
            self.cfg['maxDepth'] = 3
            self.assertEqual(len(backend.projects()), 1)

    def test_editor_and_terminal_use_argv_and_project_cwd(self):
        with tempfile.TemporaryDirectory(prefix='space ;$ ') as folder:
            self.cfg['editor'] = {'executable': '/opt/my editor', 'arguments': ['--new-window']}
            self.cfg['terminal']['arguments'] = ['--separate', '--hide-menubar']
            self.cfg['shell'] = '/usr/bin/fish'
            with patch.object(backend, 'projects', return_value=[{'path': folder}]), patch.object(backend, 'launch', return_value='accepted') as launch:
                self.assertEqual(backend.action('project', folder), 'accepted')
            self.assertEqual(launch.call_args_list[0].args[0], ['/opt/my editor', '--new-window', folder])
            self.assertEqual(launch.call_args_list[1].args[0], ['/usr/bin/konsole', '--separate', '--hide-menubar', '--workdir', folder, '-e', '/usr/bin/fish'])
            self.assertTrue(all(c.kwargs['cwd'] == Path(folder) for c in launch.call_args_list))

    def test_symlinked_project_action_uses_canonical_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / 'project'
            project.mkdir()
            (project / 'pyproject.toml').touch()
            alias = root / 'link'
            alias.symlink_to(project)
            self.cfg['projectRoots'] = [str(alias)]
            with patch.object(backend, 'launch') as launch:
                backend.action('editor', str(alias))
                self.assertEqual(launch.call_args.args[0][-1], str(project))

    def test_unknown_project_is_rejected(self):
        with patch.object(backend, 'projects', return_value=[]), patch.object(backend, 'launch') as launch:
            with self.assertRaisesRegex(ValueError, 'Unknown project'):
                backend.action('terminal', '/tmp')
            launch.assert_not_called()

    def test_ai_overrides_run_interactively_in_project(self):
        with tempfile.TemporaryDirectory(prefix='project space ') as folder:
            with patch.object(backend, 'projects', return_value=[{'path': folder}]), patch.object(backend, 'launch') as launch:
                for tool in ('codex', 'agy', 'claude'):
                    self.cfg['aiExecutables'][tool] = '/opt/my tools/' + tool
                    backend.action('ai-' + tool, folder)
                    args = launch.call_args.args[0]
                    self.assertEqual(args[-3:], ['--hold', '-e', '/opt/my tools/' + tool])
                    self.assertEqual(launch.call_args.kwargs['cwd'], Path(folder))
                    self.assertEqual(args[args.index('--workdir') + 1], folder)

    def test_workspace_reports_partial_launch_failure(self):
        with patch.object(backend, 'projects', return_value=[{'path': '/tmp'}]), patch.object(backend, 'launch', return_value='accepted'), patch.object(backend, 'terminal', side_effect=OSError('missing')):
            with self.assertRaisesRegex(ValueError, 'Editor request accepted, but terminal failed'):
                backend.action('project', '/tmp')

    def test_launch_reports_early_nonzero_exit(self):
        child = Mock()
        child.wait.return_value = 3
        with patch.object(backend.subprocess, 'Popen', return_value=child):
            with self.assertRaisesRegex(ValueError, 'exited with status 3'):
                backend.launch(['/bin/test'])

    def test_launch_acceptance_is_not_startup_confirmation(self):
        child = Mock()
        child.wait.side_effect = subprocess.TimeoutExpired('test', 0.3)
        with patch.object(backend.subprocess, 'Popen', return_value=child):
            self.assertIn('startup is not confirmed', backend.launch(['/bin/test']))

    def test_docker_target_precedence(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(backend, 'checked', return_value='desktop-linux\n'):
            self.assertEqual(backend.docker_target()['argv'], ['/usr/bin/docker', '--context', 'desktop-linux'])
        with patch.dict(os.environ, {'DOCKER_CONTEXT': 'env-context', 'DOCKER_HOST': 'unix:///tmp/other'}, clear=True):
            self.assertEqual(backend.docker_target()['argv'], ['/usr/bin/docker', '--context', 'env-context'])
            self.cfg['dockerContext'] = 'configured'
            self.assertEqual(backend.docker_target()['argv'], ['/usr/bin/docker', '--context', 'configured'])
            self.cfg['dockerContext'] = None
        with patch.dict(os.environ, {'DOCKER_HOST': 'unix:///tmp/docker.sock'}, clear=True):
            self.assertEqual(backend.docker_target()['argv'], ['/usr/bin/docker', '--host', 'unix:///tmp/docker.sock'])

    def test_docker_context_applied_to_discovery_and_all_actions(self):
        target = ['/usr/bin/docker', '--context', 'desktop-linux']
        self.cfg['dockerContext'] = 'desktop-linux'
        with patch.object(backend, 'checked', return_value='') as checked:
            self.assertTrue(backend.services()['dockerAvailable'])
            self.assertEqual(checked.call_args_list[0].args[0], target + ['ps', '-a', '--no-trunc', '--format', '{{json .}}'])
        for kind in backend.CONTAINER_ACTIONS:
            with self.subTest(kind=kind), patch.object(backend, 'checked', return_value='exited') as checked, patch.object(backend, 'terminal') as terminal:
                backend.action(kind, 'a' * 64, target)
                self.assertEqual(checked.call_args_list[0].args[0][:3], target)
                if kind == 'logs':
                    self.assertEqual(terminal.call_args.kwargs['command'][:3], target)
                elif kind == 'inspect':
                    self.assertEqual(json.loads(terminal.call_args.kwargs['command'][3]), target)
                else:
                    self.assertEqual(checked.call_args_list[-1].args[0][:3], target)

    def test_docker_target_change_rejected_before_action(self):
        self.cfg['dockerContext'] = 'new-context'
        with patch.object(backend, 'checked') as checked:
            with self.assertRaisesRegex(ValueError, 'target changed'):
                backend.action('stop', 'a' * 12, ['/usr/bin/docker', '--context', 'old'])
            checked.assert_not_called()

    def test_docker_failures_are_distinct_from_empty_results(self):
        self.cfg['dockerContext'] = 'desktop-linux'
        for category in ('unavailable', 'permission', 'timeout', 'error'):
            with self.subTest(category=category), patch.object(backend, 'checked', side_effect=[backend.CommandError(category, 'failure'), '']):
                result = backend.services()
                self.assertFalse(result['dockerAvailable'])
                self.assertEqual(result['docker']['status'], category)
                self.assertEqual(result['docker']['context'], 'desktop-linux')
        with patch.object(backend, 'checked', return_value=''):
            result = backend.services()
            self.assertTrue(result['dockerAvailable'])
            self.assertEqual(result['docker']['status'], 'ok')
            self.assertEqual(result['rows'], [])
            self.assertIn('No containers', result['docker']['message'])

    def test_command_failure_classification(self):
        for outcome, category in [(FileNotFoundError(), 'unavailable'), (PermissionError(), 'permission'), (subprocess.TimeoutExpired('docker', 6), 'timeout'), (subprocess.CompletedProcess([], 1, '', 'permission denied'), 'permission'), (subprocess.CompletedProcess([], 1, '', 'Cannot connect to the Docker daemon'), 'unavailable')]:
            with self.subTest(category=category), patch.object(backend.subprocess, 'run') as run:
                if isinstance(outcome, Exception):
                    run.side_effect = outcome
                else:
                    run.return_value = outcome
                with self.assertRaises(backend.CommandError) as caught:
                    backend.checked(['docker'])
                self.assertEqual(caught.exception.category, category)

    def test_running_or_paused_container_cannot_be_removed(self):
        for state in ('running', 'paused', 'restarting', ''):
            with self.subTest(state=state), patch.object(backend, 'docker_target', return_value={'argv': ['docker']}), patch.object(backend, 'checked', return_value=state) as checked:
                with self.assertRaises(ValueError):
                    backend.action('remove', 'a' * 12)
                self.assertEqual(checked.call_count, 1)

    def test_stopped_removal_retains_volumes_and_never_forces(self):
        for state in ('exited', 'created', 'dead'):
            with self.subTest(state=state), patch.object(backend, 'docker_target', return_value={'argv': ['docker', '--context', 'desktop-linux']}), patch.object(backend, 'checked', return_value=state) as checked:
                backend.action('remove', 'a' * 12)
                self.assertEqual(checked.call_args.args[0], ['docker', '--context', 'desktop-linux', 'rm', 'a' * 12])

    def test_invalid_container_id_is_rejected(self):
        with patch.object(backend, 'checked') as checked:
            with self.assertRaises(ValueError):
                backend.action('stop', 'abc; touch /tmp/injected')
            checked.assert_not_called()

    def process_mocks(self, eligible=True):
        rows = [{'pid': '42', 'canStop': True, 'identity': '100'}]
        return patch.multiple(backend, services=Mock(return_value={'rows': rows}), eligible_process=Mock(return_value=eligible), pidfd_supported=Mock(return_value=True))

    def test_pidfd_probe_failure_disables_controls_without_signaling(self):
        backend.pidfd_supported.cache_clear()
        self.addCleanup(backend.pidfd_supported.cache_clear)
        with patch.object(backend.os, 'pidfd_open', side_effect=OSError('kernel API unavailable')), patch.object(backend.signal, 'pidfd_send_signal') as send:
            self.assertFalse(backend.pidfd_supported())
            send.assert_not_called()

    def test_process_uses_pidfd_and_graceful_signal(self):
        with self.process_mocks(), patch.object(backend.os, 'pidfd_open', return_value=99), patch.object(backend.os, 'close') as close, patch.object(backend.signal, 'pidfd_send_signal') as send:
            backend.action('terminate', json.dumps({'pid': '42', 'identity': '100'}))
            send.assert_called_once_with(99, signal.SIGTERM)
            close.assert_called_once_with(99)
            self.assertEqual(backend.eligible_process.call_count, 2)

    def test_process_identity_or_ownership_change_after_pidfd_rejected(self):
        with self.process_mocks(), patch.object(backend, 'eligible_process', side_effect=[True, False]), patch.object(backend.os, 'pidfd_open', return_value=99), patch.object(backend.os, 'close') as close, patch.object(backend.signal, 'pidfd_send_signal') as send:
            with self.assertRaisesRegex(ValueError, 'changed'):
                backend.action('kill', json.dumps({'pid': '42', 'identity': '100'}))
            send.assert_not_called()
            close.assert_called_once_with(99)

    def test_system_process_is_rejected_without_opening_pidfd(self):
        with self.process_mocks(False), patch.object(backend.os, 'pidfd_open') as opened:
            with self.assertRaises(ValueError):
                backend.action('kill', json.dumps({'pid': '42', 'identity': '100'}))
            opened.assert_not_called()

    def test_pidfd_unavailable_disables_termination(self):
        with patch.object(backend, 'pidfd_supported', return_value=False), patch.object(backend, 'services') as services:
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                backend.action('terminate', json.dumps({'pid': '42', 'identity': '100'}))
            services.assert_not_called()

    def test_eligibility_requires_identity_all_uids_and_canonical_project_cwd(self):
        self.cfg['projectRoots'] = ['/work/projects']
        def read(path, *args, **kwargs):
            return 'Uid:\t1000\t1000\t1000\t1000\n'
        with patch.object(backend, 'pidfd_supported', return_value=True), patch.object(backend, 'process_identity', return_value='100'), patch.object(backend.os, 'getuid', return_value=1000), patch.object(Path, 'stat', return_value=Mock(st_uid=1000)), patch.object(Path, 'read_text', read), patch.object(backend.os, 'readlink', return_value='/work/projects/app') as cwd:
            self.assertTrue(backend.eligible_process('42', '100'))
            self.assertFalse(backend.eligible_process('42', '101'))
            cwd.return_value = '/work/projects-other/app'
            self.assertFalse(backend.eligible_process('42', '100'))
            cwd.return_value = '/work/projects/app'
            with patch.object(Path, 'read_text', return_value='Uid:\t1000\t0\t1000\t1000\n'):
                self.assertFalse(backend.eligible_process('42', '100'))
            with patch.object(Path, 'stat', return_value=Mock(st_uid=0)):
                self.assertFalse(backend.eligible_process('42', '100'))

    def test_browser_rejects_nonlocal_and_invalid_ports(self):
        with patch.object(backend, 'launch') as launch:
            for url in ('https://example.com', 'http://localhost:0', 'http://localhost:65536', 'http://localhost:3000/path', 'http://localhost:3000;id'):
                with self.assertRaises(ValueError):
                    backend.action('browser', url)
            launch.assert_not_called()
            backend.action('browser', 'http://localhost:3000')
            launch.assert_called_once_with(['/usr/bin/xdg-open', 'http://localhost:3000'])

    def test_clipboard_backend_detection(self):
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-0'}, clear=True), patch.object(backend.subprocess, 'run') as run:
            backend.action('copy', 'path with spaces')
            self.assertEqual(run.call_args.args[0], ['/usr/bin/wl-copy'])
            self.assertEqual(run.call_args.kwargs['input'], 'path with spaces')
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Clipboard unavailable'):
                backend.action('copy', 'test')


class ConfigurationTests(unittest.TestCase):
    def test_missing_configuration_uses_safe_defaults_without_writing(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(backend, 'CONFIG', Path(folder) / 'config.json'):
            self.assertEqual(backend.settings()['projectRoots'], ['~/Projects'])
            self.assertFalse(backend.CONFIG.exists())

    def test_valid_config_is_preserved_and_defaults_merged(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(backend, 'CONFIG', Path(folder) / 'config.json'):
            text = '{"projectRoots": [], "aiExecutables": {"codex": "~/custom bin/codex"}}\n'
            backend.CONFIG.write_text(text)
            cfg = backend.settings()
            self.assertEqual(cfg['projectRoots'], [])
            self.assertEqual(cfg['aiExecutables']['codex'], '~/custom bin/codex')
            self.assertEqual(cfg['terminal']['executable'], 'konsole')
            self.assertEqual(backend.CONFIG.read_text(), text)

    def test_invalid_configuration_fails_closed(self):
        values = [[], {'projectRoots': 'home'}, {'projectRoots': ['relative']}, {'projectRoots': [3]}, {'maxDepth': True}, {'maxDepth': -1}, {'maxDepth': 13}, {'editor': 'code'}, {'editor': {'executable': 'code', 'arguments': '-n'}}, {'terminal': {'executable': ''}}, {'aiExecutables': []}, {'aiExecutables': {'codex': 3}}, {'dockerContext': 3}, {'dockerContext': '-bad'}, {'shell': False}, {'typo': 1}]
        with tempfile.TemporaryDirectory() as folder, patch.object(backend, 'CONFIG', Path(folder) / 'config.json'):
            for value in values:
                with self.subTest(value=value):
                    backend.CONFIG.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        backend.settings()
            backend.CONFIG.write_text('{malformed')
            with self.assertRaisesRegex(ValueError, 'Configuration error'):
                backend.settings()

    def test_executable_resolution_uses_stable_mise_shims_and_user_bin(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin'}, clear=True), patch.object(backend.shutil, 'which', return_value='/home/test/.local/share/mise/shims/codex') as which:
            backend.executable('codex')
            path = which.call_args.kwargs['path']
            self.assertIn(str(Path.home() / '.local/share/mise/shims'), path.split(os.pathsep))
            self.assertIn(str(Path.home() / '.local/bin'), path.split(os.pathsep))
            self.assertNotIn('installs/node/', path)
        with patch.object(backend.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'mise reshim'):
                backend.executable('codex')


if __name__ == '__main__':
    unittest.main()
