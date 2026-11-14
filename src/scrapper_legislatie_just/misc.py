import hashlib
import random
import re
import time
from functools import wraps
from pathlib import Path

from bs4 import BeautifulSoup, Comment
from unidecode import unidecode

# Romanian letters with diacritics, including the old cedilla variants that
# still appear in legislation.just.ro (ş/ţ) next to the comma-below ones.
_DIACRITICS = str.maketrans(
    {
        "ă": "a",
        "Ă": "A",
        "â": "a",
        "Â": "A",
        "î": "i",
        "Î": "I",
        "ș": "s",
        "Ș": "S",
        "ț": "t",
        "Ț": "T",
        "ş": "s",
        "Ş": "S",
        "ţ": "t",
        "Ţ": "T",
    }
)


_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _transliterate(match: re.Match[str]) -> str:
    char = match.group(0)
    return unidecode(char) if char.isalpha() else char


def strip_diacritics(value: str) -> str:
    """Fold accented letters to plain ASCII, without touching layout.

    Romanian diacritics are replaced explicitly so box-drawing characters in
    the ASCII tables, quotes and dashes stay intact. Any other accented letter
    that survives is transliterated with ``unidecode``.
    """
    text = str(value).translate(_DIACRITICS)
    if text.isascii():
        return text
    return _NON_ASCII.sub(_transliterate, text)


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
