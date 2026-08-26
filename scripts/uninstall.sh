#!/usr/bin/env bash
set -euo pipefail

launcher_target="${HOME}/.local/bin/ai"
config_target="${HOME}/.config/ai-launcher/agents.toml"
history_target="${HOME}/.local/share/ai-launcher/history.tsv"

rm -f -- "${launcher_target}"
printf 'Removed launcher: %s\n' "${launcher_target}"

if [[ "${1:-}" == "--purge" ]]; then
    rm -f -- "${config_target}" "${history_target}"
    printf 'Removed config and history.\n'
else
    printf 'Preserved config and history. Use --purge to remove them too.\n'
fi
