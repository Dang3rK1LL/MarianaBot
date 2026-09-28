"""Open the VPS chat using a private, local SSH connection profile."""

import json
import os
import re
import subprocess
from pathlib import Path


def main():
    profile = Path(__file__).resolve().parent.parent / "mariana-server.json"
    try:
        config = json.loads(profile.read_text(encoding="utf-8"))
        host, user = config["host"], config["user"]
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", host):
            raise ValueError("Invalid server hostname")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
            raise ValueError("Invalid SSH username")
        key = Path(config["identity_file"]).expanduser()
        if not key.is_file():
            raise ValueError("The SSH key file was not found")
        environment = dict(os.environ)
        if environment.get("TERM") in (None, "", "dumb"):
            environment["TERM"] = "xterm-256color"
        return subprocess.call(
            [
                "ssh",
                "-t",
                "-i",
                str(key),
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ServerAliveInterval=30",
                "-o",
                "ServerAliveCountMax=3",
                f"{user}@{host}",
                "~/.local/bin/mariana",
            ],
            env=environment,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"Cannot connect: {exc}. See docs/cloud-server.md.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
