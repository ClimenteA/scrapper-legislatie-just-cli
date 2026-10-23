import random
import time
from functools import wraps
from pathlib import Path


def jitter_call(low: float = 1, high: float = 3):
    """Sleep for a random interval before calling the wrapped function."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = random.uniform(low, high)
            time.sleep(delay)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def extract_file_meta(filename: str) -> dict | None:
    """Parse ``<number>-<slug>-<YYYY-MM-DD>.html`` file names."""
    import re

    pattern = r"(\d+)-(.*)-(\d{4}-\d{2}-\d{2})"
    match = re.search(pattern, Path(filename).name)

    if not match:
        return None

    return {
        "numar_link": match.group(1),
        "slug": match.group(2),
        "data_consolidarii": match.group(3),
    }
