"""Tests for database/recovery.py."""
import pytest
from pathlib import Path

from plexmix.database.recovery import DatabaseRecovery


class TestEnsureDatabaseExists:
    def test_creates_new_db(self, tmp_path):
        db_path = str(tmp_path / "new.db")
        result = DatabaseRecovery.ensure_database_exists(db_path)
        assert result is False  # newly created
        assert Path(db_path).exists()

    def test_existing_db_returns_true(self, tmp_path):
        db_path = str(tmp_path / "existing.db")
        # Create it first
        DatabaseRecovery.initialize_database(db_path)
        result = DatabaseRecovery.ensure_database_exists(db_path)
        assert result is True


class TestInitializeDatabase:
    def test_creates_all_tables(self, tmp_path):
        db_path = str(tmp_path / "init.db")
        DatabaseRecovery.initialize_database(db_path)
        assert Path(db_path).exists()

        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "tracks" in tables
        assert "artists" in tables
        assert "albums" in tables

    def test_creates_parent_dirs(self, tmp_path):
        db_path = str(tmp_path / "nested" / "dir" / "db.db")
        DatabaseRecovery.initialize_database(db_path)
        assert Path(db_path).exists()


class TestVerifyDatabaseIntegrity:
    def test_valid_db(self, tmp_path):
        db_path = str(tmp_path / "valid.db")
        DatabaseRecovery.initialize_database(db_path)
        assert DatabaseRecovery.verify_database_integrity(db_path) is True

    def test_missing_file(self, tmp_path):
        db_path = str(tmp_path / "missing.db")
        assert DatabaseRecovery.verify_database_integrity(db_path) is False

    def test_missing_tables(self, tmp_path):
        """DB that triggers an exception returns False.
        Note: SQLiteManager.connect() auto-creates tables, so a DB with only
        a dummy table will get schema created on open. We test the exception
        path instead by making the file unreadable."""
        db_path = str(tmp_path / "bad.db")
        # Write garbage that looks like a DB header but isn't valid
        Path(db_path).write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
        assert DatabaseRecovery.verify_database_integrity(db_path) is False

    def test_exception_returns_false(self, tmp_path):
        """Corrupted file that can't be opened."""
        db_path = str(tmp_path / "corrupt.db")
        Path(db_path).write_text("this is not a sqlite file")
        assert DatabaseRecovery.verify_database_integrity(db_path) is False


class TestRecoverOrRecreate:
    def test_missing_db_creates_new(self, tmp_path):
        db_path = str(tmp_path / "missing.db")
        result = DatabaseRecovery.recover_or_recreate(db_path)
        assert "missing" in result.lower() or "Created" in result
        assert Path(db_path).exists()

    def test_corrupted_db_backs_up_and_recreates(self, tmp_path):
        """Corrupted DB triggers backup + recreate."""
        db_path = str(tmp_path / "corrupt.db")
        # Write garbage that will fail integrity check
        Path(db_path).write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

        result = DatabaseRecovery.recover_or_recreate(db_path)
        assert "corrupted" in result.lower() or "Backed up" in result
        # Original path should now be a fresh DB
        assert Path(db_path).exists()

    def test_healthy_db_returns_message(self, tmp_path):
        db_path = str(tmp_path / "healthy.db")
        DatabaseRecovery.initialize_database(db_path)
        result = DatabaseRecovery.recover_or_recreate(db_path)
        assert "healthy" in result.lower()


class TestGetSafeManager:
    def test_existing_db_returns_manager(self, tmp_path):
        db_path = str(tmp_path / "existing.db")
        DatabaseRecovery.initialize_database(db_path)
        manager = DatabaseRecovery.get_safe_manager(db_path)
        assert manager is not None
        manager.close()

    def test_auto_recover_creates_db(self, tmp_path):
        db_path = str(tmp_path / "auto.db")
        manager = DatabaseRecovery.get_safe_manager(db_path, auto_recover=True)
        assert Path(db_path).exists()
        manager.close()

    def test_no_auto_recover_raises(self, tmp_path):
        db_path = str(tmp_path / "noauto.db")
        with pytest.raises(FileNotFoundError):
            DatabaseRecovery.get_safe_manager(db_path, auto_recover=False)
