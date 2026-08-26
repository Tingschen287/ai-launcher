#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin_dir="${HOME}/.local/bin"
config_dir="${HOME}/.config/ai-launcher"
launcher_target="${bin_dir}/ai"
config_target="${config_dir}/agents.toml"

install -d "${bin_dir}" "${config_dir}"
install -m 0755 "${repo_dir}/src/ai_launcher.py" "${launcher_target}"

if [[ ! -e "${config_target}" ]]; then
    install -m 0644 "${repo_dir}/config/agents.example.toml" "${config_target}"
    printf 'Created config: %s\n' "${config_target}"
else
    printf 'Preserved existing config: %s\n' "${config_target}"
fi

printf 'Installed Agent Deck: %s\n' "${launcher_target}"
printf 'Run `ai --list` to verify the agent registry.\n'
