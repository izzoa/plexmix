"""Copy of the library that is made before a large removal.

The copy holds the database and, when they exist, the vector index file and its
metadata file. It is written to ``<database folder>/backups/<YYYYMMDD_HHMMSS>/``, the
place and naming ``plexmix db reset`` uses. Nothing here deletes an old backup.
"""

import logging
import shutil
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .sqlite_manager import SQLiteManager

logger = logging.getLogger(__name__)

BACKUPS_FOLDER_NAME = "backups"

# How long the copy waits for a database that another connection keeps locked.
_LOCK_WAIT_SECONDS = 30.0

# SQLite result codes for "busy" and "locked". The sqlite3 module names them only
# from Python 3.11 on.
_SQLITE_BUSY = 5
_SQLITE_LOCKED = 6

# Folder names tried for one second: <stamp>, <stamp>_2, <stamp>_3, ...
_MAX_FOLDERS_PER_SECOND = 100


class BackupError(Exception):
    """The backup could not be made. No partial backup folder is left behind."""


@dataclass(frozen=True)
class BackupResult:
    """Where a backup was written and how many bytes it takes."""

    folder: Path
    size_bytes: int


def backup_library(db: SQLiteManager, index_path: Optional[Path]) -> BackupResult:
    """Copy the database and the index files into a new dated backup folder.

    The database is copied through SQLite's online backup, so changes that are still
    in the write-ahead log are in the copy. A plain file copy can miss them. The index
    file and its ``.metadata`` file are copied when they exist.

    Call this outside a transaction: uncommitted changes cannot be copied.

    Raises:
        BackupError: the backup could not be made. The partial folder is removed first.
    """
    db_path = db.db_path
    backups_folder = db_path.parent / BACKUPS_FOLDER_NAME
    folder: Optional[Path] = None
    try:
        if not db_path.is_file():
            # Do not let get_connection() create an empty database only to copy it.
            raise BackupError(f"There is no database file at {db_path}")
        source = db.get_connection()
        if source.in_transaction:
            # Connection.backup() retries without end while its own connection holds
            # a write transaction, so refuse here.
            raise BackupError("The database has changes that are not committed yet")

        folder = _create_backup_folder(backups_folder)
        _copy_database(source, folder / db_path.name)
        for path in _index_files(index_path):
            _copy_file(path, folder)
        size_bytes = sum(item.stat().st_size for item in folder.iterdir() if item.is_file())
    except BaseException as exc:
        # BaseException: Ctrl+C must not leave half a backup that looks complete.
        if folder is not None:
            _remove_partial_folder(folder)
        if isinstance(exc, BackupError) or not isinstance(exc, Exception):
            raise
        raise BackupError(f"Could not write a backup in {backups_folder}: {_reason(exc)}") from exc

    logger.info("Backed up the library to %s (%d bytes)", folder, size_bytes)
    return BackupResult(folder=folder, size_bytes=size_bytes)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _create_backup_folder(backups_folder: Path) -> Path:
    """Create a folder that did not exist before, so no earlier backup is overwritten."""
    stamp = _timestamp()
    for attempt in range(1, _MAX_FOLDERS_PER_SECOND + 1):
        folder = backups_folder / (stamp if attempt == 1 else f"{stamp}_{attempt}")
        try:
            folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return folder
    raise BackupError(f"Could not find an unused backup folder name in {backups_folder}")


def _copy_database(source: sqlite3.Connection, target: Path) -> None:
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS

    def stop_when_locked_too_long(status: int, remaining: int, total: int) -> None:
        # Connection.backup() retries a locked database without end. An error raised
        # here ends the copy.
        if status in (_SQLITE_BUSY, _SQLITE_LOCKED) and time.monotonic() >= deadline:
            raise BackupError(
                f"The database stayed locked for {_LOCK_WAIT_SECONDS:.0f} seconds, "
                "so it could not be copied"
            )

    with closing(sqlite3.connect(str(target))) as destination:
        source.backup(destination, progress=stop_when_locked_too_long)


def _index_files(index_path: Optional[Path]) -> List[Path]:
    """The index file and its metadata file, each only when it exists."""
    if index_path is None:
        return []
    index_file = Path(index_path).expanduser()
    # VectorIndex.save_index names the metadata file the same way.
    candidates = [index_file, index_file.with_suffix(".metadata")]
    return [path for path in dict.fromkeys(candidates) if path.exists()]


def _copy_file(source: Path, folder: Path) -> None:
    target = folder / source.name
    if target.exists():
        raise BackupError(f"Two files to back up are both named {source.name}")
    shutil.copy2(source, target)


def _remove_partial_folder(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        logger.warning("Could not remove the incomplete backup folder %s", folder)


def _reason(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__
