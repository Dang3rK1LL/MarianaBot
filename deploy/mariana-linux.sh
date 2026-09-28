#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$HOME/MarianaBot"
export PATH="$HOME/.local/bin:$HOME/MarianaBot/.venv/bin:$PATH"
if [[ $# == 0 || ( $# == 1 && "$1" == "chat" ) ]]; then
    systemctl --user start marianabot-terminal.service
    # A tmux client must not silently create a server outside the systemd service.
    for _ in {1..50}; do
        if tmux -L marianabot show-options -g exit-empty >/dev/null 2>&1; then
            exec tmux -L marianabot new-session -A -s mariana -c "$HOME/MarianaBot" \
                /usr/bin/bash "$HOME/MarianaBot/deploy/chat-linux.sh"
        fi
        sleep 0.1
    done
    echo "MarianaBot's terminal service did not start. Check systemctl --user status marianabot-terminal." >&2
    exit 1
fi
exec .venv/bin/mariana "$@" --data-dir "$HOME/.local/share/marianabot"
