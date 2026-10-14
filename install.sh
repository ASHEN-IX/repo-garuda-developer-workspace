#!/usr/bin/env bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_root="${XDG_CONFIG_HOME:-$HOME/.config}"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_root="${XDG_BIN_HOME:-$HOME/.local/bin}"
target="$data_root/garuda-developer-workspace"
for root in "$config_root" "$data_root" "$bin_root"; do
  [[ "$root" = /* ]] || { echo "XDG paths must be absolute: $root" >&2; exit 1; }
done
for dependency in python3 flock xdg-open ss; do
  command -v "$dependency" >/dev/null || {
    echo "Missing dependency: $dependency. See README.md for Garuda package/setup commands." >&2
    exit 1
  }
done
qs_bin="$(command -v quickshell || command -v qs || true)"
if [[ -z "$qs_bin" ]]; then
  echo 'Missing Quickshell 0.3.x. Install it with: sudo pacman -Syu quickshell' >&2
  exit 1
fi
version="$("$qs_bin" --version 2>&1)"
if [[ ! "$version" =~ (^|[^0-9])0\.3\.[0-9]+ ]]; then
  echo "Quickshell 0.3.x is required; detected: $version" >&2
  exit 1
fi
python3 "$source_dir/plugin/backend.py" check-dependencies
# Finish dependency checks before touching the installation. Configuration is never replaced.
mkdir -p "$target/plugin" "$config_root/garuda-developer-workspace" "$bin_root" "$data_root/applications" "$data_root/icons/hicolor/scalable/apps"
for name in shell.qml Developer.qml ListSync.js backend.py; do
  install -m 644 "$source_dir/plugin/$name" "$target/plugin/$name"
done
install -m 644 "$source_dir/LICENSE" "$target/LICENSE"
install -m 644 "$source_dir/README.md" "$target/README.md"
if [[ ! -e "$config_root/garuda-developer-workspace/config.json" ]]; then
  install -m 600 "$source_dir/plugin/config.json" "$config_root/garuda-developer-workspace/config.json"
fi
install -m 755 "$source_dir/bin/garuda-developer-workspace" "$bin_root/garuda-developer-workspace"
install -m 755 "$source_dir/uninstall.sh" "$target/uninstall.sh"
install -m 644 "$source_dir/assets/garuda-developer-workspace.svg" "$data_root/icons/hicolor/scalable/apps/garuda-developer-workspace.svg"
# Desktop Exec quoting is not shell quoting; escape its reserved characters explicitly.
python3 - "$bin_root/garuda-developer-workspace" "$data_root/applications/garuda-developer-workspace.desktop" <<'PY'
from pathlib import Path
import sys
path = sys.argv[1]
if '\n' in path or '\r' in path:
    raise SystemExit('Install paths cannot contain newlines')
escaped = ''.join('\\' + c if c in '\\"`$' else c for c in path).replace('%', '%%')
# Desktop string unescaping precedes Exec parsing, so backslashes are doubled again.
escaped = escaped.replace('\\', '\\\\')
Path(sys.argv[2]).write_text(f'''[Desktop Entry]
Type=Application
Name=Garuda Developer Workspace
Comment=Projects, coding tools, Docker containers and local services
Exec="{escaped}"
Icon=garuda-developer-workspace
Terminal=false
Categories=Development;Utility;
StartupNotify=true
Actions=Services;

[Desktop Action Services]
Name=Development Services
Exec="{escaped}" services
''')
PY
if command -v update-desktop-database >/dev/null; then update-desktop-database "$data_root/applications"; fi
if command -v gtk-update-icon-cache >/dev/null && [[ -f "$data_root/icons/hicolor/index.theme" ]]; then
  gtk-update-icon-cache -q "$data_root/icons/hicolor" || true
fi
printf 'Installed Garuda Developer Workspace. Launch:\n  %q\n' "$bin_root/garuda-developer-workspace"
if [[ ":$PATH:" != *":$bin_root:"* ]]; then
  printf 'Add the launcher to fish PATH:\n  fish_add_path %q\n' "$bin_root"
fi
printf 'Settings preserved at %s\nClose any running workspace before reopening after an update.\n' "$config_root/garuda-developer-workspace/config.json"
