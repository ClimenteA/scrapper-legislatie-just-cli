import hashlib
import random
import time
from functools import wraps
from pathlib import Path

from bs4 import BeautifulSoup, Comment


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


def html_content_hash(path: str | Path) -> str:
    """Hash the content of an HTML file, ignoring serialization noise.

    Monolith writes a ``Saved from ...`` comment with the download timestamp
    and version, and different monolith versions can serialize tags slightly
    differently. Both change the raw bytes without changing the law text, so
    the hash is computed on a re-serialized DOM with comments removed.
    """
    soup = BeautifulSoup(Path(path).read_bytes(), "html5lib")
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    return hashlib.sha256(soup.encode("utf-8")).hexdigest()
