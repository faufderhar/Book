from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("WINDVANE_DATA")
    if override:
        path = Path(override)
    else:
        path = repo_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sqlite_path() -> Path:
    return data_dir() / "windvane.sqlite"


def config_path(name: str) -> Path:
    return repo_root() / "config" / name
