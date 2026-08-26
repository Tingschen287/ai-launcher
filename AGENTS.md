# AGENTS.md

## Scope

Agent Deck is intended to become cross-platform, but the current supported runtime is
WSL/Linux. Windows-native and macOS adapters are roadmap items, not current behavior.

## Sources of truth

- Launcher implementation: `src/ai_launcher.py`
- Shareable configuration: `config/agents.example.toml`
- WSL installation: `scripts/install.sh`
- Windows Terminal example: `integrations/windows-terminal/profiles.example.jsonc`

Do not commit a user's live `agents.toml`, terminal settings, history, credentials,
tokens, or absolute home-directory paths.

## Change protocol

1. Keep the launcher dependency-free unless a dependency has a clear portability payoff.
2. Put machine-specific behavior behind configuration or a platform-specific adapter.
3. Do not claim Windows-native or macOS support until it has an installer and a tested input/launch backend.
4. Preserve existing configuration during install and upgrade.
5. Run `python3 -m unittest discover -s tests -v` and `bash -n scripts/*.sh` before committing.
