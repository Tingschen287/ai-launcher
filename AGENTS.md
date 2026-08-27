# AGENTS.md

## Scope

Agent Deck is intended to become cross-platform, but the current supported runtime is
WSL/Linux. Windows-native and macOS adapters are roadmap items, not current behavior.

## Sources of truth

- Shared TUI: `src/deck_tui.py`
- Agent Deck: `src/ai_launcher.py`
- Host Deck: `src/host_deck.py`
- Host Deck secrets: `src/host_secrets.py`
- Shareable configuration: `config/agents.example.toml`, `config/hosts.example.toml`
- WSL installation: `scripts/install.sh`
- Windows Terminal example: `integrations/windows-terminal/profiles.example.jsonc`

Do not commit a user's live `agents.toml`, `hosts.toml`, terminal settings, history,
SSH config, credentials, tokens, or absolute home-directory paths.

Host Deck treats `~/.ssh/config` as the connection source of truth. Its own config
only stores UI metadata (name, group, color, favorite, remote dir, tmux session).
Passwords go to the OS credential store (Windows Credential Manager from WSL) and
are injected at connect time with SSH_ASKPASS. Never write passwords into toml,
ssh config, tests fixtures, or Git. Do not uninstall or rewrite Tabby.

## Change protocol

1. Keep the launcher dependency-free unless a dependency has a clear portability payoff.
2. Put machine-specific behavior behind configuration or a platform-specific adapter.
3. Do not claim Windows-native or macOS support until it has an installer and a tested input/launch backend.
4. Preserve existing configuration during install and upgrade.
5. Run `python3 -m unittest discover -s tests -v` and `bash -n scripts/*.sh` before committing.
