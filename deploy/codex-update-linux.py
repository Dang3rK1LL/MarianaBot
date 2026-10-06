"""Update the standalone Codex CLI only while MarianaBot research is idle."""

import json
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path

from filelock import FileLock, Timeout


def main():
    repo = Path(__file__).resolve().parent.parent
    directories = [Path.home() / ".local/share/marianabot", repo / ".mariana"]
    try:
        with ExitStack() as locks:
            for directory in directories:
                if not directory.is_dir():
                    continue
                locks.enter_context(FileLock(str(directory / "launch.lock"), timeout=0))
                locks.enter_context(FileLock(str(directory / "worker.lock"), timeout=0))
                metadata = directory / "worker.json"
                if metadata.exists():
                    record = json.loads(metadata.read_text(encoding="utf-8"))
                    if (
                        record.get("state") == "starting"
                        and time.time() - record.get("started", 0) < 20
                    ):
                        print("Research is starting; Codex update deferred.", flush=True)
                        return
            subprocess.run(
                [str(Path.home() / ".local/bin/codex"), "update"], check=True, timeout=180
            )
    except Timeout:
        print("Research is active; Codex update deferred.", flush=True)


if __name__ == "__main__":
    main()
