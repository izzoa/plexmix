import logging
from pathlib import Path
from typing import Optional
from .sqlite_manager import SQLiteManager

logger = logging.getLogger(__name__)


class DatabaseRecovery:
    """Handle database file deletion and recovery scenarios"""
    
    @staticmethod
    def ensure_database_exists(db_path: str) -> bool:
        """
        Check if database exists and initialize if missing.
        
        Args:
            db_path: Path to the database file
            
        Returns:
            True if database was already present, False if newly created
        """
        path = Path(db_path).expanduser()
        db_existed = path.exists()
        
        if not db_existed:
            logger.warning(f"Database not found at {path}. Initializing new database...")
            DatabaseRecovery.initialize_database(db_path)
            logger.info(f"New database created at {path}")
            return False
        
        return True
    
    @staticmethod
    def initialize_database(db_path: str) -> None:
        """
        Create and initialize a new database with all required tables.
        
        Args:
            db_path: Path where the database should be created
        """
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with SQLiteManager(db_path) as db:
            db.create_tables()
            logger.info(f"Database initialized successfully at {path}")
    
    @staticmethod
    def verify_database_integrity(db_path: str) -> bool:
        """
        Verify that database has all required tables and structure.
        
        Args:
            db_path: Path to the database file
            
        Returns:
            True if database is valid, False otherwise
        """
        try:
            path = Path(db_path).expanduser()
            
            if not path.exists():
                logger.error(f"Database file does not exist: {path}")
                return False
            
            with SQLiteManager(db_path) as db:
                cursor = db.get_connection().cursor()
                
                required_tables = [
                    'artists', 'albums', 'tracks', 'genres', 
                    'track_genres', 'embeddings', 'sync_history', 
                    'playlists', 'playlist_tracks', 'tracks_fts'
                ]
                
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                existing_tables = {row[0] for row in cursor.fetchall()}
                
                missing_tables = set(required_tables) - existing_tables
                
                if missing_tables:
                    logger.error(f"Database missing required tables: {missing_tables}")
                    return False
                
                logger.info("Database integrity check passed")
                return True
                
        except Exception as e:
            logger.error(f"Database integrity check failed: {e}")
            return False
    
    @staticmethod
    def recover_or_recreate(db_path: str) -> str:
        """
        Attempt to recover database or create new one.
        
        Args:
            db_path: Path to the database file
            
        Returns:
            Status message describing what was done
        """
        path = Path(db_path).expanduser()
        
        if not path.exists():
            DatabaseRecovery.initialize_database(db_path)
            return f"Database was missing. Created new database at {path}. Run 'plexmix sync' to populate."
        
        if not DatabaseRecovery.verify_database_integrity(db_path):
            backup_path = path.parent / f"{path.stem}_corrupted_{path.suffix}"
            path.rename(backup_path)
            logger.warning(f"Corrupted database backed up to {backup_path}")
            
            DatabaseRecovery.initialize_database(db_path)
            return f"Database was corrupted. Backed up to {backup_path} and created new database. Run 'plexmix sync' to populate."
        
        return "Database is healthy"
    
    @staticmethod
    def get_safe_manager(db_path: str, auto_recover: bool = True) -> SQLiteManager:
        """
        Get a SQLiteManager with automatic recovery if database is missing.
        
        Args:
            db_path: Path to the database file
            auto_recover: Whether to automatically create database if missing
            
        Returns:
            SQLiteManager instance
            
        Raises:
            FileNotFoundError: If database doesn't exist and auto_recover is False
        """
        path = Path(db_path).expanduser()
        
        if not path.exists():
            if auto_recover:
                logger.warning(f"Database not found at {path}. Auto-recovering...")
                DatabaseRecovery.initialize_database(db_path)
            else:
                raise FileNotFoundError(
                    f"Database not found at {path}. Run 'plexmix sync' to create it."
                )
        
        return SQLiteManager(db_path)
