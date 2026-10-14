#!/usr/bin/env python3
"""Garuda Developer Workspace: local discovery and explicit, constrained actions."""
import copy
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent
_config_root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
CONFIG = (_config_root if _config_root.is_absolute() else Path.home() / '.config') / 'garuda-developer-workspace/config.json'
DEFAULTS = {
    'projectRoots': ['~/Projects'], 'maxDepth': 4,
    'editor': {'executable': 'code', 'arguments': []},
    'terminal': {'executable': 'konsole', 'arguments': ['--separate']},
    'shell': None, 'aiExecutables': {'codex': None, 'claude': None, 'agy': None},
    'dockerContext': None,
}
MARKERS = ('.git', 'package.json', 'pyproject.toml', 'Cargo.toml', 'go.mod',
           'composer.json', 'pom.xml', 'build.gradle', 'build.gradle.kts', 'setup.py', 'Gemfile')
SKIP = {'.git', 'node_modules', '.venv', 'venv', '.next', 'dist', 'build', 'target',
        '.cache', 'vendor', 'graphify-out', '__pycache__', 'coverage'}
CONTAINER_ACTIONS = {'start', 'stop', 'restart', 'remove', 'inspect', 'logs'}


class CommandError(ValueError):
    def __init__(self, category, message):
        self.category = category
        super().__init__(message)


def valid_string(value):
    return isinstance(value, str) and bool(value.strip()) and '\0' not in value


def settings():
    """Read only. Invalid settings fail closed, including for destructive actions."""
    cfg = copy.deepcopy(DEFAULTS)
    try:
        data = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as error:
        raise ValueError(f'Configuration error in {CONFIG}: {error}') from error
    if not isinstance(data, dict):
        raise ValueError('Configuration must be a JSON object')
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ValueError('Unknown configuration keys: ' + ', '.join(sorted(unknown)))
    cfg.update(data)
    roots = cfg['projectRoots']
    if not isinstance(roots, list) or not all(valid_string(r) and Path(r).expanduser().is_absolute() for r in roots):
        raise ValueError('projectRoots must be an array of absolute or ~/ paths')
    if type(cfg['maxDepth']) is not int or not 0 <= cfg['maxDepth'] <= 12:
        raise ValueError('maxDepth must be an integer from 0 to 12')
    for name in ('editor', 'terminal'):
        tool = cfg[name]
        if not isinstance(tool, dict) or set(tool) - {'executable', 'arguments'} or not valid_string(tool.get('executable')):
            raise ValueError(f'{name} must contain executable and optional arguments')
        arguments = tool.setdefault('arguments', [])
        if not isinstance(arguments, list) or not all(isinstance(a, str) and '\0' not in a for a in arguments):
            raise ValueError(f'{name}.arguments must be an array of strings')
    agents = cfg['aiExecutables']
    if not isinstance(agents, dict) or set(agents) - {'codex', 'claude', 'agy'}:
        raise ValueError('aiExecutables must be an object with codex, claude, or agy overrides')
    for key, value in agents.items():
        if value is not None and not valid_string(value):
            raise ValueError(f'aiExecutables.{key} must be an executable path/name or null')
    for key in ('dockerContext', 'shell'):
        if cfg[key] is not None and not valid_string(cfg[key]):
            raise ValueError(f'{key} must be a nonempty string or null')
    if cfg['dockerContext'] and cfg['dockerContext'].startswith('-'):
        raise ValueError('dockerContext cannot begin with a dash')
    return cfg


def execution_env():
    env = os.environ.copy()
    # Stable user paths work from KDE even when its PATH lacks interactive-shell additions.
    extras = [str(Path.home() / '.local/bin'), str(Path.home() / '.local/share/mise/shims')]
    env['PATH'] = os.pathsep.join([env.get('PATH', os.defpath)] + extras)
    return env


def executable(name):
    expanded = str(Path(name).expanduser())
    if '/' in expanded and not Path(expanded).is_absolute():
        raise ValueError('Executable paths must be absolute: ' + name)
    result = shutil.which(expanded, path=execution_env()['PATH'])
    if not result:
        raise CommandError('unavailable', f'{name} was not found or is not executable. Set its executable in {CONFIG}; mise users can run mise reshim.')
    return str(Path(result).absolute())


def checked(args, timeout=6):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=execution_env())
    except FileNotFoundError as error:
        raise CommandError('unavailable', f'{args[0]} is not installed') from error
    except subprocess.TimeoutExpired as error:
        raise CommandError('timeout', f'{Path(args[0]).name} timed out after {timeout}s') from error
    except PermissionError as error:
        raise CommandError('permission', f'Permission denied executing {args[0]}') from error
    except OSError as error:
        raise CommandError('unavailable', str(error)) from error
    if result.returncode:
        message = (result.stderr.strip() or result.stdout.strip() or 'Command failed')[:500]
        if any(s in message.lower() for s in ('permission denied', 'access denied', 'operation not permitted')):
            category = 'permission'
        elif any(s in message.lower() for s in ('cannot connect to the docker daemon', 'connection refused', 'is the docker daemon running')):
            category = 'unavailable'
        else:
            category = 'error'
        raise CommandError(category, message)
    return result.stdout


def normalized(path):
    return Path(path).expanduser().resolve()


def project_roots(cfg=None):
    roots = set(normalized(r) for r in (cfg or settings())['projectRoots'])
    # Walk overlapping roots once; symlink aliases share the same canonical path.
    return sorted((r for r in roots if not any(r != other and r.is_relative_to(other) for other in roots)), key=str)


def projects(cfg=None):
    cfg = cfg or settings()
    found = {}
    for root in project_roots(cfg):
        if not root.is_dir():
            continue
        for folder, dirs, files in os.walk(root, followlinks=False):
            path = Path(folder)
            depth = len(path.relative_to(root).parts)
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.') and not (path / d).is_symlink())
            if depth >= cfg['maxDepth']:
                dirs[:] = []
            if any((path / marker).exists() for marker in MARKERS):
                dirs[:] = []  # A monorepo is one project; never execute its scripts.
                path = normalized(path)
                detail = str(path.relative_to(Path.home())) if path.is_relative_to(Path.home()) else str(path)
                found[str(path)] = {'name': path.name, 'detail': detail, 'path': str(path), 'kind': 'project'}
    return sorted(found.values(), key=lambda row: (row['name'].lower(), row['path']))


def docker_target(cfg=None):
    cfg = cfg or settings()
    docker = executable('docker')
    if cfg['dockerContext']:
        context, source = cfg['dockerContext'], 'configuration'
    elif os.environ.get('DOCKER_CONTEXT'):
        context, source = os.environ['DOCKER_CONTEXT'], 'DOCKER_CONTEXT'
    elif os.environ.get('DOCKER_HOST'):
        host = os.environ['DOCKER_HOST']
        parsed = urlsplit(host)
        safe_host = parsed._replace(netloc=parsed.netloc.rsplit('@', 1)[-1]).geturl()
        return {'argv': [docker, '--host', host], 'label': 'host ' + safe_host, 'source': 'DOCKER_HOST'}
    else:
        context, source = checked([docker, 'context', 'show']).strip(), 'selected context'
    if not context or context.startswith('-'):
        raise ValueError('Docker did not return a valid context')
    return {'argv': [docker, '--context', context], 'label': context, 'source': source}


@lru_cache(maxsize=1)
def pidfd_supported():
    if not callable(getattr(os, 'pidfd_open', None)) or not callable(getattr(signal, 'pidfd_send_signal', None)):
        return False
    try:
        # Probe only this backend process; no signal is sent.
        fd = os.pidfd_open(os.getpid())
        os.close(fd)
        return True
    except OSError:
        return False


def process_identity(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return ''


def eligible_process(pid, identity, cfg=None):
    if not pidfd_supported() or int(pid) <= 1 or int(pid) == os.getpid() or not identity:
        return False
    try:
        proc = Path(f'/proc/{pid}')
        uids = re.search(r'^Uid:\s+(.*)$', (proc / 'status').read_text(), re.MULTILINE)
        owned = proc.stat().st_uid == os.getuid() and uids and all(int(uid) == os.getuid() for uid in uids[1].split())
        cwd = normalized(os.readlink(proc / 'cwd'))
        return bool(owned and process_identity(pid) == identity and any(cwd.is_relative_to(root) for root in project_roots(cfg)))
    except (OSError, ValueError):
        return False


def services(cfg=None):
    cfg = cfg or settings()
    rows = []
    docker = {'available': False, 'status': 'unavailable', 'context': 'unresolved', 'source': '', 'message': '', 'target': []}
    try:
        target = docker_target(cfg)
        docker.update(context=target['label'], source=target['source'], target=target['argv'])
        output = checked(target['argv'] + ['ps', '-a', '--no-trunc', '--format', '{{json .}}'])
        for line in output.splitlines():
            c = json.loads(line)
            rows.append({'kind': 'container', 'name': c['Names'], 'detail': c['Status'] + ' · ' + (c['Ports'] or 'No published ports'),
                         'id': c['ID'], 'image': c['Image'], 'state': c.get('State', 'unknown'), 'status': c['Status'], 'ports': c['Ports']})
        docker.update(available=True, status='ok', message='' if output.strip() else 'No containers in this Docker target')
    except CommandError as error:
        docker.update(status=error.category, message=str(error))
    except (ValueError, KeyError, TypeError) as error:
        rows = []
        docker.update(status='error', message='Docker discovery failed: ' + str(error))
    listener_error = ''
    try:
        output = checked([executable('ss'), '-ltnpH'])
    except CommandError as error:
        output, listener_error = '', str(error)
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        address = fields[3]
        try:
            port = int(address.rsplit(':', 1)[1])
        except ValueError:
            continue
        if not 1024 <= port <= 65535:
            continue
        match = re.search(r'users:\(\("([^"\n]+)",pid=(\d+)', line)
        process, pid = (match[1], match[2]) if match else ('Listener', '')
        if process in ('code', 'chrome', 'chromium', 'language_server', 'quickshell'):
            continue
        cwd = ''
        if pid:
            try:
                cwd = str(normalized(os.readlink(f'/proc/{pid}/cwd')))
            except OSError:
                pass
        http = process.lower() in ('node', 'bun', 'deno', 'python', 'python3', 'uvicorn', 'gunicorn', 'next-server', 'vite') or port in (3000, 3001, 4000, 4173, 4200, 5000, 5050, 5173, 8000, 8080, 8081)
        identity = process_identity(pid) if pid else ''
        rows.append({'kind': 'port', 'name': f'{process} · :{port}', 'detail': address + (' · ' + cwd if cwd else ''),
                     'port': port, 'pid': pid, 'identity': identity, 'canStop': bool(pid and eligible_process(pid, identity, cfg)),
                     'state': 'listening', 'url': f'http://localhost:{port}' if http else ''})
    return {'rows': rows, 'dockerAvailable': docker['available'], 'docker': docker, 'listenerError': listener_error,
            'processControls': pidfd_supported()}


def launch(args, cwd=None):
    """Detect exec and early exit failures. Acceptance is not proof of a mapped window."""
    with tempfile.TemporaryFile(mode='w+t') as errors:
        child = subprocess.Popen(args, cwd=str(cwd) if cwd else None, env=execution_env(), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True)
        try:
            code = child.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            return 'Launch request accepted; application startup is not confirmed.'
        if code:
            errors.seek(0)
            raise ValueError(f'{Path(args[0]).name} exited with status {code}: {errors.read(400).strip()}')
    return 'Launch request accepted; application startup is not confirmed.'


def terminal(path=None, command=None, cfg=None):
    cfg = cfg or settings()
    directory = normalized(path or Path.home())
    tool = cfg['terminal']
    args = [executable(tool['executable'])] + tool['arguments'] + ['--workdir', str(directory)]
    if command:
        args += ['--hold', '-e'] + command
    else:
        args += ['-e', executable(cfg['shell'] or os.environ.get('SHELL') or '/usr/bin/fish')]
    return launch(args, cwd=directory)


def action(kind, value, expected_target=None):
    cfg = settings()
    if kind in ('project', 'editor', 'terminal', 'files', 'ai-codex', 'ai-claude', 'ai-agy'):
        path = normalized(value)
        if str(path) not in {p['path'] for p in projects(cfg)}:
            raise ValueError('Unknown project; refresh after checking configured project roots')
        if kind.startswith('ai-'):
            name = kind[3:]
            return terminal(path, [executable(cfg['aiExecutables'].get(name) or name)], cfg)
        if kind == 'files':
            return launch([executable('xdg-open'), str(path)], cwd=path)
        if kind in ('project', 'editor'):
            editor = cfg['editor']
            message = launch([executable(editor['executable'])] + editor['arguments'] + [str(path)], cwd=path)
        if kind in ('project', 'terminal'):
            try:
                message = terminal(path, cfg=cfg)
            except (ValueError, OSError) as error:
                if kind == 'project':
                    raise ValueError('Editor request accepted, but terminal failed: ' + str(error)) from error
                raise
        return message
    if kind in CONTAINER_ACTIONS:
        if not re.fullmatch(r'[0-9a-f]{12,64}', value):
            raise ValueError('Invalid container ID')
        target = docker_target(cfg)['argv']
        if expected_target is not None and target != expected_target:
            raise ValueError('Docker target changed; refresh and confirm the action again')
        state = checked(target + ['inspect', '--format', '{{.State.Status}}', value]).strip()
        if not state:
            raise ValueError('Container no longer exists; refresh')
        if kind == 'remove':
            if state not in ('exited', 'created', 'dead'):
                raise ValueError('Stop the container before removing it')
            checked(target + ['rm', value], timeout=25)  # Never force; retain all volumes.
        elif kind in ('start', 'stop', 'restart'):
            checked(target + [kind, value], timeout=25)
        elif kind == 'logs':
            return terminal(command=target + ['logs', '--tail', '150', '--follow', value], cfg=cfg)
        else:
            # No shell pipeline: this helper uses the frozen argv and a pager directly.
            return terminal(command=[sys.executable, str(BASE / 'backend.py'), 'inspect-view', json.dumps(target), value], cfg=cfg)
        return 'Container action completed.'
    if kind in ('terminate', 'kill'):
        if not pidfd_supported():
            raise ValueError('Process controls unavailable: Python/Linux pidfd APIs are required')
        data = json.loads(value)
        pid, identity = int(data['pid']), data['identity']
        if not isinstance(identity, str) or not identity.isdigit() or pid <= 1:
            raise ValueError('Invalid process identity')
        matching = any(r.get('pid') == str(pid) and r.get('canStop') and r.get('identity') == identity for r in services(cfg)['rows'])
        if not matching or not eligible_process(pid, identity, cfg):
            raise ValueError('Process changed, is not yours, or is outside configured project folders')
        fd = os.pidfd_open(pid)
        try:
            if not eligible_process(pid, identity, cfg):
                raise ValueError('Process identity, ownership or project directory changed; refresh')
            signal.pidfd_send_signal(fd, signal.SIGTERM if kind == 'terminate' else signal.SIGKILL)
        finally:
            os.close(fd)
        return 'Process signal sent.'
    if kind == 'copy':
        if os.environ.get('WAYLAND_DISPLAY'):
            args = [executable('wl-copy')]
        elif os.environ.get('DISPLAY'):
            args = [executable('xclip'), '-selection', 'clipboard']
        else:
            raise ValueError('Clipboard unavailable: no Wayland or X11 session')
        subprocess.run(args, input=value, text=True, check=True, timeout=3, env=execution_env())
        return 'Copied to clipboard.'
    if kind == 'browser':
        match = re.fullmatch(r'http://localhost:([0-9]{1,5})', value)
        if not match or not 1 <= int(match[1]) <= 65535:
            raise ValueError('Invalid local service URL')
        return launch([executable('xdg-open'), value])
    raise ValueError('Unknown action')


def snapshot():
    cfg = settings()
    result = services(cfg)
    agents = []
    for name, tool in [('Codex CLI', 'codex'), ('Antigravity CLI', 'agy'), ('Claude Code', 'claude')]:
        try:
            path = executable(cfg['aiExecutables'].get(tool) or tool)
            agents.append({'name': name, 'id': tool, 'available': True, 'message': path})
        except ValueError as error:
            agents.append({'name': name, 'id': tool, 'available': False, 'message': str(error)})
    rows = result.pop('rows')
    return dict(result, projects=projects(cfg), services=rows, agents=agents, updated=time.strftime('%H:%M:%S'), configError='')


def main():
    try:
        if len(sys.argv) > 1 and sys.argv[1] == 'check-dependencies':
            cfg = settings()
            for tool in (cfg['editor']['executable'], cfg['terminal']['executable'], cfg['shell'] or os.environ.get('SHELL') or '/usr/bin/fish'):
                executable(tool)
            print('Configured editor, terminal and shell found.')
            return 0
        if len(sys.argv) > 1 and sys.argv[1] == 'inspect-view':
            target, container = json.loads(sys.argv[2]), sys.argv[3]
            output = checked(target + ['inspect', container])
            pager = shutil.which('less')
            if pager:
                subprocess.run([pager], input=output, text=True, check=True)
            else:
                print(output)
            return 0
        if len(sys.argv) > 1 and sys.argv[1] == 'action':
            target = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
            message = action(sys.argv[2], sys.argv[3], target)
            print(json.dumps({'ok': True, 'message': message}))
        else:
            print(json.dumps(snapshot()))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError, IndexError) as error:
        if len(sys.argv) > 1 and sys.argv[1] in ('inspect-view', 'check-dependencies'):
            print(str(error), file=sys.stderr)
        elif len(sys.argv) > 1 and sys.argv[1] == 'action':
            print(json.dumps({'ok': False, 'message': str(error)[:600]}))
        else:
            print(json.dumps({'projects': [], 'services': [], 'agents': [], 'dockerAvailable': False,
                              'docker': {'context': 'unresolved', 'source': '', 'target': [], 'message': '', 'status': 'unavailable'},
                              'configError': str(error)[:600], 'listenerError': '', 'processControls': False, 'updated': ''}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
