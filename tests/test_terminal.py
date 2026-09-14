import os
import subprocess
import sys


def test_dashboard_handles_legacy_output_encoding(tmp_path):
    environment = dict(os.environ, PYTHONIOENCODING="cp1250:strict")
    result = subprocess.run(
        [sys.executable, "-m", "marianabot", "demo", "--data-dir", str(tmp_path / "demo")],
        capture_output=True,
        env=environment,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert "COMPLETE" in result.stdout.decode("utf-8")
    assert list((tmp_path / "demo" / "exports").glob("*/report.md"))
