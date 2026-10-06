#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$HOME/MarianaBot"
args=(--config "$HOME/MarianaBot/discord.toml")
if [[ -f "$HOME/MarianaBot/discord-token.txt" ]]; then
    args+=(--replace)
fi
.venv/bin/python -m marianabot --no-update discord token "${args[@]}"
systemctl --user reset-failed marianabot-discord.service
systemctl --user start marianabot-discord.service
systemctl --user --no-pager status marianabot-discord.service
