"""Offline integration check on an installed Linux VPS; never calls a provider."""

import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from marianabot.config import Config
from marianabot.store import Store


def wait_for(predicate, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("Service check timed out")


def main():
    if sys.platform != "linux":
        raise SystemExit("Run this check on the Linux server")
    root = Path(__file__).resolve().parent.parent
    name = "marianabot-check-" + uuid.uuid4().hex[:12]
    unit = Path.home() / ".config/systemd/user" / (name + ".service")

    def systemctl(action):
        subprocess.run(["systemctl", "--user", action, unit.name], check=True)

    with tempfile.TemporaryDirectory(prefix="mariana-service-check-") as temporary:
        directory = Path(temporary)
        store = Store(directory)
        cfg = Config()
        cfg.research.max_rounds = 2
        cfg.research.min_rounds = 1
        cfg.rb.agents = cfg.jb.agents = 12
        run_id = store.create_run("Offline server recovery verification", cfg, demo=True)
        (directory / "worker.json").write_text(
            json.dumps({"run_id": run_id, "mode": "research", "state": "starting", "started": 0}),
            encoding="utf-8",
        )
        content = (root / "deploy/marianabot-terminal.service").read_text(encoding="utf-8")
        content = content.replace("%h/.local/share/marianabot", str(directory))
        content = content.replace("[Service]", "[Service]\nEnvironment=MARIANA_AUTO_UPDATE=0")
        content = content.replace("%h/MarianaBot", str(root))
        content = content.replace("-L marianabot", "-L " + name)
        unit.write_text(content, encoding="utf-8")
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            systemctl("start")
            wait_for(lambda: any(c["state"] == "done" for c in store.calls(run_id)))
            completed = {c["id"] for c in store.calls(run_id) if c["state"] == "done"}
            systemctl("stop")
            stopped = store.run(run_id)
            assert stopped["status"] == "paused", stopped
            assert stopped["reason"] == "Worker interrupted; resume to continue", stopped
            systemctl("start")
            wait_for(lambda: store.run(run_id)["status"] == "complete")
            calls = store.calls(run_id)
            assert completed <= {c["id"] for c in calls if c["state"] == "done"}
            tasks = [c["task"] for c in calls if c["state"] == "done"]
            assert len(tasks) == len(set(tasks)), "A completed task was repeated"
            systemctl("stop")
            store.update_run(
                run_id, status="paused", control="pause", reason="Owner requested pause"
            )
            before = len(store.calls(run_id))
            systemctl("start")
            time.sleep(1)
            assert store.run(run_id)["status"] == "paused"
            assert len(store.calls(run_id)) == before
            print("PASS: startup, interruption, checkpoint reuse, and owner pause after restart.")
        finally:
            subprocess.run(["systemctl", "--user", "stop", unit.name], check=False)
            unit.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            store.close()


if __name__ == "__main__":
    main()
