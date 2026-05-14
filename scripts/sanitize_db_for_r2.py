"""Strip outreach / contacts / waves rows from a DB copy so it's safe to
upload to R2. The source DB is untouched; the destination is a fresh copy
with those three tables emptied.

Usage:
    python scripts/sanitize_db_for_r2.py <source.db> <destination.db>
"""
from __future__ import annotations
import shutil
import sqlite3
import sys
from pathlib import Path


def sanitize(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        raise SystemExit(
            "ERROR: source and destination must be different (got same path). "
            "Refusing to mutate the source DB in place."
        )
    if not src.exists():
        raise SystemExit(f"ERROR: source DB not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    conn = sqlite3.connect(dst)
    try:
        conn.executescript(
            "DELETE FROM outreach;\n"
            "DELETE FROM contacts;\n"
            "DELETE FROM waves;\n"
            "VACUUM;"
        )
        conn.commit()
    finally:
        conn.close()
    print(f"Sanitized DB written to {dst}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: sanitize_db_for_r2.py <source.db> <destination.db>")
    sanitize(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
