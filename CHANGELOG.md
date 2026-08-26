# Changelog

## 0.4.1 - 2026-08-26

- Change Resume from automatic latest-session continuation to each Agent's native session picker.
- Use Grok's `/resume` TUI command to open its interactive Resume page directly.

## 0.4.0 - 2026-08-26

- Add config-driven resume commands for every bundled Agent.
- Add keyboard and mouse resume actions to the shared directory page.
- Support direct resume with `ai --resume <agent> <directory>`.

## 0.3.0 - 2026-08-26

- Add mouse-motion tracking and hover highlighting to every clickable row.
- Preserve keyboard selection while providing pointer-specific visual feedback.

## 0.2.2 - 2026-08-26

- Make proxy routing explicit and process-local for every Agent launch.
- Prevent `.proxy.sh` auto-enable behavior from overriding `proxy = false`.
- Configure Claude via CC-Switch to use a direct connection.

## 0.2.1 - 2026-08-26

- Show each Agent's actual proxy policy in directory views.
- Label global proxy availability separately from per-Agent routing.

## 0.2.0 - 2026-08-26

- Replace the single-line manual path prompt with live child-directory suggestions.
- Add keyboard and mouse selection, Tab/right-arrow drill-down, and path filtering.
- Share the enhanced directory picker across every configured Agent.

## 0.1.0 - 2026-08-26

- Add the keyboard-first Agent and directory picker.
- Launch Claude Official, Claude via CC-Switch, Codex, Grok, and Kimi from TOML configuration.
- Preserve per-Agent environment, proxy, PATH, color, and Windows Terminal profile behavior.
- Add reusable WSL install/uninstall scripts and Windows Terminal profile examples.
- Add regression coverage for empty terminal input in both menus.
