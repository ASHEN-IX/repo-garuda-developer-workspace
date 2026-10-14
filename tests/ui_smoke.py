#!/usr/bin/env python3
"""Opt-in Quickshell runtime smoke test using ONLY a synthetic backend.

Run: python3 tests/ui_smoke.py [--screenshots /tmp/gdw-preview]
Set GDW_QUICKSHELL for a runtime outside PATH. No real services/tools are called.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshots', type=Path)
    parser.add_argument('--light', action='store_true', help='Test the light color branch with a synthetic palette')
    args = parser.parse_args()
    qs = os.environ.get('GDW_QUICKSHELL') or shutil.which('quickshell') or shutil.which('qs')
    if not qs:
        raise SystemExit('Quickshell 0.3.x required for the opt-in UI test')
    with tempfile.TemporaryDirectory(prefix='gdw-ui-space ') as directory:
        root = Path(directory)
        shutil.copytree(REPO / 'plugin', root / 'plugin')
        shutil.copytree(REPO / 'bin', root / 'bin')
        entry = root / 'plugin/shell.qml'
        qml = root / 'plugin/Developer.qml'
        source = qml.read_text()
        if args.light:
            source = source.replace('fg: desktopPalette.text', 'fg: "#20242a"').replace('bg: desktopPalette.window', 'bg: "#f5f6f8"')
        harness = '''
 IpcHandler {
  target:"smoke"
  function select(index: int): void { list.currentIndex=index }
  function searchFor(value: string): void { search.text=value }
  function scrollTo(value: real): void { list.contentY=value }
  function refresh(): void { root.refresh() }
  function ask(): void { root.ask("stop", "Stop container", "Synthetic confirmation") }
  function cancel(): void { root.pending=null }
  function runSyntheticAction(): void { root.act("logs", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") }
  function nativeClose(): void { panel.contentItem.Window.window.close() }
  function resize(width: int, height: int): void { panel.contentItem.Window.window.width=width;panel.contentItem.Window.window.height=height }
  function capture(path: string): void { card.grabToImage(function(result) {result.saveToFile(path)}) }
  function state(): string { return JSON.stringify({status:JSON.parse(root.status()),scroll:list.contentY,notice:root.notice,width:panel.width,height:panel.height,minWidth:panel.contentItem.Window.window.minimumWidth,minHeight:panel.contentItem.Window.window.minimumHeight,implicitWidth:panel.implicitWidth,windowVisible:panel.contentItem.Window.window.visible,backingVisible:panel.backingWindowVisible}) }
 }
'''
        source = source.replace(' IpcHandler {', harness + '\n IpcHandler {', 1)
        qml.write_text(source)
        projects = [{'kind': 'project', 'name': f'Demo project {i:02}', 'detail': f'Projects/demo {i}', 'path': f'/synthetic/projects/demo {i}'} for i in range(24)]
        fixture = dict(projects=projects, services=[{'kind': 'container', 'id': 'a' * 64, 'name': 'demo-api', 'detail': 'Up · 3000', 'state': 'running', 'image': 'demo:latest', 'ports': '3000', 'status': 'Up'}, {'kind': 'port', 'name': 'node · :5173', 'detail': '127.0.0.1:5173 · /synthetic/projects/demo', 'port': 5173, 'pid': '42', 'identity': '100', 'canStop': True, 'state': 'listening', 'url': 'http://localhost:5173'}], agents=[{'name': 'Codex CLI', 'id': 'codex', 'available': False, 'message': 'Demo: configure Codex or run mise reshim'}, {'name': 'Claude Code', 'id': 'claude', 'available': True, 'message': '/synthetic/bin/claude'}], dockerAvailable=True, docker={'context': 'desktop-linux', 'source': 'selected context', 'target': ['/synthetic/docker', '--context', 'desktop-linux'], 'status': 'ok', 'message': ''}, configError='', listenerError='', processControls=True, updated='12:00:00')
        (root / 'fixture.json').write_text(json.dumps(fixture))
        (root / 'plugin/backend.py').write_text('''import json, pathlib, sys, time
root = pathlib.Path(__file__).parent.parent
if len(sys.argv) > 1:
    (root / 'action-args').write_text(json.dumps(sys.argv[1:]))
    time.sleep(0.6)
    print(json.dumps({'ok': True, 'message': 'Synthetic action accepted'}))
else:
    with (root / 'scans').open('a') as log: log.write('scan\\n')
    print((root / 'fixture.json').read_text())
''')
        env = dict(os.environ, GDW_QUICKSHELL=qs, QT_QUICK_CONTROLS_STYLE='Basic')

        def ipc(target, function, *values, check=True):
            return subprocess.run([qs, 'ipc', '--path', str(entry), 'call', target, function, *map(str, values)], env=env, capture_output=True, text=True, timeout=10, check=check).stdout.strip()

        def state():
            return json.loads(ipc('smoke', 'state'))

        def wait_for(predicate, seconds=10):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    if predicate():
                        return
                except (ValueError, subprocess.CalledProcessError):
                    pass
                time.sleep(0.1)
            raise AssertionError('Timed out waiting for UI state')

        def scans():
            return (root / 'scans').read_text().count('scan')

        try:
            launcher = [str(root / 'bin/garuda-developer-workspace')]
            # Concurrent first launches exercise the startup lock, not just a warm IPC call.
            children = [subprocess.Popen(launcher, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
            for child in children:
                out, err = child.communicate(timeout=20)
                assert child.returncode == 0, out + err
            wait_for(lambda: state()['status']['rows'] == 24 and not state()['status']['scanning'])
            wait_for(lambda: state()['width'] >= 840 and state()['height'] >= 620)
            listed = subprocess.run([qs, 'list', '--path', str(entry), '--json'], env=env, check=True, capture_output=True, text=True)
            assert len(json.loads(listed.stdout)) == 1, listed.stdout
            ipc('smoke', 'select', 12)
            ipc('smoke', 'scrollTo', 700)
            before = state()
            count = scans()
            ipc('smoke', 'refresh')
            wait_for(lambda: scans() > count and not state()['status']['scanning'])
            after = state()
            assert after['status']['selectedKey'] == before['status']['selectedKey']
            assert abs(after['scroll'] - before['scroll']) < 1
            ipc('smoke', 'searchFor', 'Demo project 03')
            wait_for(lambda: state()['status']['rows'] == 1)
            ipc('smoke', 'searchFor', '')
            if args.screenshots:
                ipc('smoke', 'resize', 1060, 800)
                wait_for(lambda: state()['width'] == 1060 and state()['height'] == 800)
                args.screenshots.mkdir(parents=True, exist_ok=True)
                ipc('smoke', 'capture', str(args.screenshots.resolve() / 'projects.png'))
                wait_for(lambda: (args.screenshots / 'projects.png').exists())
            subprocess.run(launcher + ['services'], env=env, check=True, capture_output=True, timeout=15)
            wait_for(lambda: state()['status']['rows'] == 2 and not state()['status']['scanning'])
            ipc('smoke', 'ask')
            assert state()['status']['pending'] and not state()['status']['polling']
            count = scans()
            ipc('smoke', 'refresh')
            time.sleep(0.2)
            assert scans() == count
            ipc('smoke', 'cancel')
            assert state()['status']['polling']
            ipc('smoke', 'runSyntheticAction')
            assert state()['status']['acting'] and not state()['status']['polling']
            wait_for(lambda: not state()['status']['acting'] and not state()['status']['scanning'])
            sent = json.loads((root / 'action-args').read_text())
            assert json.loads(sent[-1]) == fixture['docker']['target'], sent
            if args.screenshots:
                ipc('smoke', 'capture', str(args.screenshots.resolve() / 'services.png'))
                wait_for(lambda: (args.screenshots / 'services.png').exists())
            ipc('smoke', 'resize', 840, 620)
            wait_for(lambda: state()['width'] == 840 and state()['height'] == 620)
            ipc('smoke', 'nativeClose')
            wait_for(lambda: not state()['status']['opened'])
            count = scans()
            time.sleep(5.2)  # Longer than one polling interval; no real discovery ever runs.
            assert scans() == count and not state()['status']['polling']
            subprocess.run(launcher + ['services'], env=env, check=True, capture_output=True, timeout=15)
            wait_for(lambda: state()['status']['opened'] and state()['status']['visible'])
            print('PASS: concurrent launch, one instance, search, selection/scroll refresh, confirmation pause, resize, native close, hidden polling, action pause/context argv, reopen')
            logs = subprocess.run([qs, 'log', '--path', str(entry), '--tail', '100'], env=env, capture_output=True, text=True).stdout
            failures = [line for line in logs.splitlines() if 'WARN' in line and 'qt.qpa.services' not in line or 'ERROR' in line or 'ReferenceError' in line or 'TypeError' in line]
            assert not failures, '\n'.join(failures)
        except Exception:
            print('UI failure state:', ipc('smoke', 'state', check=False))
            print(subprocess.run([qs, 'log', '--path', str(entry), '--tail', '100'], env=env, capture_output=True, text=True).stdout)
            raise
        finally:
            ipc('workspace', 'quit', check=False)


if __name__ == '__main__':
    main()
