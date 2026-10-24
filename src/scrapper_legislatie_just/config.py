import os
from pathlib import Path

DATA_ROOT_NAME = "legislatie-descarcata"

# Folder names are prefixed with the pipeline step that writes into them.
STEP_DIRS = {
    "raw": "1-raw",
    "clean": "2-clean",
    "expanded": "3-expanded",
    "processed": "4-processed",
}

DATA_SUBDIRS = tuple(STEP_DIRS.values())


def get_default_data_dir() -> Path:
    """Use ``SCRAPPER_DATA_DIR`` when set, otherwise ``<cwd>/legislatie-descarcata``."""
    env_dir = os.getenv("SCRAPPER_DATA_DIR")
    return (
        Path(env_dir).expanduser()
        if env_dir
        else Path.cwd() / DATA_ROOT_NAME
    )


def step_dir(root: str | Path, step: str) -> Path:
    """Return the folder for a pipeline step name (raw/clean/expanded/processed)."""
    if step not in STEP_DIRS:
        raise ValueError(f"Unknown step folder: {step}")
    return Path(root) / STEP_DIRS[step]


def resolve_data_dir(data_dir: str | Path | None = None) -> Path:
    """Return the data root, creating it (and the standard layout) if needed."""
    root = Path(data_dir).expanduser() if data_dir else get_default_data_dir()
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name in DATA_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root
