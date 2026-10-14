#!/usr/bin/env bash
set -euo pipefail
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --purge-config ) ]]; then
  echo 'Usage: ./uninstall.sh [--purge-config]' >&2
  exit 2
fi
config_root="${XDG_CONFIG_HOME:-$HOME/.config}"
data_root="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_root="${XDG_BIN_HOME:-$HOME/.local/bin}"
for root in "$config_root" "$data_root" "$bin_root"; do
  [[ "$root" = /* ]] || { echo "XDG paths must be absolute: $root" >&2; exit 1; }
done
entry="$data_root/garuda-developer-workspace/plugin/shell.qml"
qs_bin="$(command -v quickshell || command -v qs || true)"
if [[ -n "$qs_bin" && -f "$entry" ]]; then
  "$qs_bin" ipc --path "$entry" call workspace quit >/dev/null 2>&1 || true
fi
rm -f -- "$bin_root/garuda-developer-workspace" "$data_root/applications/garuda-developer-workspace.desktop" "$data_root/icons/hicolor/scalable/apps/garuda-developer-workspace.svg"
rm -rf -- "$data_root/garuda-developer-workspace"
if [[ "${1:-}" = --purge-config ]]; then
  rm -rf -- "$config_root/garuda-developer-workspace"
  echo 'Uninstalled; configuration removed.'
else
  echo "Uninstalled; configuration preserved in $config_root/garuda-developer-workspace."
fi
if command -v update-desktop-database >/dev/null; then update-desktop-database "$data_root/applications"; fi
