"""Create an independent, consistent, read-only preview data snapshot."""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import uuid
from pathlib import Path, PurePosixPath


def _stored_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value.replace("\\", "/"))
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"Unsafe stored media path: {value!r}")
    path = (root / Path(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Stored media path escapes the data directory: {value!r}")
    return path


def create_preview_snapshot(source: str | Path, destination: str | Path) -> Path:
    """Copy a live SQLite database consistently, then copy its immutable media.

    The destination must not exist. Source and destination must be separate trees.
    The source is only opened for reading; the snapshot contains no upload staging.
    """
    source_root = Path(source).resolve(strict=True)
    destination_path = Path(destination).resolve()
    database = source_root / "database" / "assets.sqlite3"
    originals = source_root / "originals"
    thumbnails = source_root / "thumbnails"
    if not database.is_file() or not originals.is_dir() or not thumbnails.is_dir():
        raise FileNotFoundError("Source must contain database/assets.sqlite3, originals/, and thumbnails/")
    if destination_path.exists():
        raise FileExistsError(f"Snapshot destination already exists: {destination_path}")
    if destination_path == source_root or destination_path.is_relative_to(source_root):
        raise ValueError("Snapshot destination cannot be inside the source data directory")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.parent / f".{destination_path.name}.tmp-{uuid.uuid4().hex}"
    try:
        (temporary / "database").mkdir(parents=True)
        source_uri = database.as_uri() + "?mode=ro"
        with sqlite3.connect(source_uri, uri=True, timeout=30) as source_db:
            snapshot_db = sqlite3.connect(temporary / "database" / "assets.sqlite3", timeout=30)
            try:
                source_db.backup(snapshot_db)
                integrity = snapshot_db.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(f"Snapshot database integrity check failed: {integrity}")
                media_paths = snapshot_db.execute("SELECT path,thumbnail FROM files").fetchall()
            finally:
                snapshot_db.close()

        shutil.copytree(originals, temporary / "originals")
        shutil.copytree(thumbnails, temporary / "thumbnails")
        for original, thumbnail in media_paths:
            if not _stored_path(temporary, original).is_file():
                raise FileNotFoundError(f"Snapshot is missing original media: {original}")
            if not _stored_path(temporary, thumbnail).is_file():
                raise FileNotFoundError(f"Snapshot is missing thumbnail: {thumbnail}")
        os.rename(temporary, destination_path)
        return destination_path
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Existing writable Team Assets data directory")
    parser.add_argument("destination", type=Path, help="New snapshot directory; it must not already exist")
    args = parser.parse_args()
    result = create_preview_snapshot(args.source, args.destination)
    print(f"Created preview snapshot at {result}")


if __name__ == "__main__":
    main()
