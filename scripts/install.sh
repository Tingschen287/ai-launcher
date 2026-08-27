#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin_dir="${HOME}/.local/bin"
lib_dir="${HOME}/.local/lib/deck"
config_dir="${HOME}/.config/ai-launcher"
host_config_dir="${HOME}/.config/host-deck"
launcher_target="${bin_dir}/ai"
host_target="${bin_dir}/host"
config_target="${config_dir}/agents.toml"
host_config_target="${host_config_dir}/hosts.toml"

install -d "${bin_dir}" "${lib_dir}" "${config_dir}" "${host_config_dir}"
install -m 0644 "${repo_dir}/src/deck_tui.py" "${lib_dir}/deck_tui.py"
install -m 0644 "${repo_dir}/src/host_secrets.py" "${lib_dir}/host_secrets.py"
install -m 0644 "${repo_dir}/src/tabby_import.py" "${lib_dir}/tabby_import.py"
install -m 0644 "${repo_dir}/src/host_askpass.ps1" "${lib_dir}/host_askpass.ps1"
install -m 0644 "${repo_dir}/src/host_askpass.cmd" "${lib_dir}/host_askpass.cmd"
install -m 0755 "${repo_dir}/src/ai_launcher.py" "${lib_dir}/ai_launcher.py"
install -m 0755 "${repo_dir}/src/host_deck.py" "${lib_dir}/host_deck.py"
ln -sfn "${lib_dir}/ai_launcher.py" "${launcher_target}"
ln -sfn "${lib_dir}/host_deck.py" "${host_target}"

if [[ ! -e "${config_target}" ]]; then
    install -m 0644 "${repo_dir}/config/agents.example.toml" "${config_target}"
    printf 'Created config: %s\n' "${config_target}"
else
    printf 'Preserved existing config: %s\n' "${config_target}"
fi

if [[ ! -e "${host_config_target}" ]]; then
    install -m 0644 "${repo_dir}/config/hosts.bootstrap.toml" "${host_config_target}"
    printf 'Created config: %s\n' "${host_config_target}"
else
    printf 'Preserved existing config: %s\n' "${host_config_target}"
fi

printf 'Installed Agent Deck: %s\n' "${launcher_target}"
printf 'Installed Host Deck: %s\n' "${host_target}"
printf 'Run `ai --list` and `host --list` to verify.\n'
