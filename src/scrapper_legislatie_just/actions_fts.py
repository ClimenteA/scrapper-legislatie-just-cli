"""FTS5 index for the ANAF action sheets (declaration metadata).

The ``actions`` table already stores each form's code, title and description,
harvested from the declaration manifest. Building ``actions_fts`` reads only
those columns: it does **not** re-parse, re-convert or re-OCR any PDF. This is
why the upgrade command is fast compared to a full index rebuild.

Codes come in several shapes (``300``, ``D300``, ``D_212_2736_2025``), so the
indexed text also contains separator-free variants; otherwise FTS5 would split
``D_212_2736_2025`` into ``d``/``212``/``2736``/``2025`` and a natural ``D212``
query would not match the single token ``d21227362025``.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from .config import resolve_data_dir, step_dir

FTS_TABLE = "actions_fts"

CREATE_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    doc_slug UNINDEXED,
    code UNINDEXED,
    search_text,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""

# Reuse the same expression when building the FTS table and when refreshing it
# from an existing index, so both paths produce byte-identical indexed text.
_COMPACT = "replace(replace(replace(code, '_', ''), '-', ''), ' ', '')"
SEARCH_TEXT_SQL = f"""
lower(
    code || ' ' ||
    {_COMPACT} || ' ' ||
    'd' || {_COMPACT} || ' ' ||
    'declaratia-' || {_COMPACT} || ' ' ||
    coalesce(title, '') || ' ' || coalesce(description, '')
)
"""

REFRESH_SQL = f"""
INSERT INTO {FTS_TABLE}(rowid, doc_slug, code, search_text)
SELECT rowid, doc_slug, code, {SEARCH_TEXT_SQL}
FROM actions
"""


def ensure_actions_fts(connection: sqlite3.Connection) -> int:
    """Create (or rebuild) ``actions_fts`` from the existing ``actions`` table.

    Returns the number of indexed declarations. Only the small metadata table
    is read, so no Docling/PDF work happens here.
    """
    connection.execute(CREATE_SQL)
    connection.execute(f"DELETE FROM {FTS_TABLE}")
    connection.execute(REFRESH_SQL)
    return connection.execute(f"SELECT count(*) FROM {FTS_TABLE}").fetchone()[0]


def refresh_actions_fts(
    db_path: str | Path, *, dry_run: bool = False
) -> dict[str, object]:
    """Add/refresh ``actions_fts`` in an existing ``law.db`` atomically.

    The database is copied to a temporary sibling, the FTS table is written
    there, then ``os.replace`` publishes it in one step. A running FastAPI
    worker with an open read-only connection keeps reading the old inode until
    it is restarted, so no partially written index is ever observed.
    """
    db = Path(db_path).expanduser().resolve()
    if not db.exists():
        raise FileNotFoundError(f"Index not found: {db}")

    tmp = db.with_name(db.name + ".actions-fts.tmp")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(db, tmp)

    connection = sqlite3.connect(tmp)
    try:
        count = ensure_actions_fts(connection)
        connection.commit()
    finally:
        connection.close()

    if dry_run:
        tmp.unlink()
        return {"db": str(db), "declarations": count, "applied": False}

    os.replace(tmp, db)
    return {"db": str(db), "declarations": count, "applied": True}


def main() -> None:
    """CLI entry point: upgrade an existing index without re-reading PDFs."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Construieste actions_fts din metadata deja indexata."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Calea catre law.db (implicit: <data>/4-processed/law.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Construieste indexul temporar, dar nu inlocuieste law.db.",
    )
    args = parser.parse_args()

    db = (
        Path(args.db).expanduser().resolve()
        if args.db
        else step_dir(resolve_data_dir(), "processed") / "law.db"
    )
    result = refresh_actions_fts(db, dry_run=args.dry_run)
    verb = "verificat" if args.dry_run else "actualizat"
    print(
        f"Index FTS {verb}: {result['declarations']} declaratii "
        f"in {result['db']}."
    )


if __name__ == "__main__":
    main()
