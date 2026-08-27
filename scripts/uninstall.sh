#!/usr/bin/env bash
set -euo pipefail

launcher_target="${HOME}/.local/bin/ai"
host_target="${HOME}/.local/bin/host"
lib_dir="${HOME}/.local/lib/deck"
config_target="${HOME}/.config/ai-launcher/agents.toml"
history_target="${HOME}/.local/share/ai-launcher/history.tsv"
host_config_target="${HOME}/.config/host-deck/hosts.toml"
host_history_target="${HOME}/.local/share/host-deck/history.tsv"
host_favorites_target="${HOME}/.local/share/host-deck/favorites.txt"

rm -f -- "${launcher_target}" "${host_target}"
rm -f -- "${lib_dir}/ai_launcher.py" "${lib_dir}/host_deck.py" \
    "${lib_dir}/deck_tui.py" "${lib_dir}/host_secrets.py" \
    "${lib_dir}/tabby_import.py" \
    "${lib_dir}/host_askpass.ps1" "${lib_dir}/host_askpass.cmd"
rmdir -- "${lib_dir}" 2>/dev/null || true
printf 'Removed Agent Deck and Host Deck launchers.\n'

if [[ "${1:-}" == "--purge" ]]; then
    rm -f -- "${config_target}" "${history_target}" \
        "${host_config_target}" "${host_history_target}" "${host_favorites_target}"
    printf 'Removed config, history, and favorites.\n'
else
    printf 'Preserved config and history. Use --purge to remove them too.\n'
fi
