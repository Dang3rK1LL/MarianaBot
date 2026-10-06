"""User-selected parent folders for separate research files."""

import os
import re
from pathlib import Path


def default_work_folder() -> Path:
    return Path.home() / "MarianaBot-work"


def resolve_work_folder(value: str | Path) -> Path:
    text = str(value).strip()
    if not text:
        raise ValueError("Choose a work folder before starting research.")
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", text):
        raise ValueError("Use a folder on this host, such as ~/MarianaBot-work.")
    path = Path(text).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("The work folder must be a directory.")
    return path
