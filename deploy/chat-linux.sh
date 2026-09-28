#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$HOME/MarianaBot"
exec .venv/bin/mariana chat --data-dir "$HOME/.local/share/marianabot" --config "$HOME/MarianaBot/mariana.toml"
