import reflex as rx
from typing import List, Dict
from plexmix.ui.states.app_state import AppState


class DashboardState(AppState):
    recent_playlists: List[Dict[str, str]] = []

    @rx.event
    def on_load(self):
        """Load dashboard data when the page loads."""
        print("DashboardState.on_load called")
        self.check_configuration_status()
        self.load_library_stats()
        self.load_recent_playlists()
        return rx.console_log("Dashboard loaded")

    def load_recent_playlists(self):
        try:
            from plexmix.config.settings import Settings
            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                self.recent_playlists = []
                return

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()

            cursor.execute("""
                SELECT p.name, p.created_at, COUNT(pt.track_id) as track_count
                FROM playlists p
                LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
                GROUP BY p.id, p.name, p.created_at
                ORDER BY p.created_at DESC
                LIMIT 10
            """)
            rows = cursor.fetchall()

            self.recent_playlists = [
                {
                    "name": row[0],
                    "created_at": row[1],
                    "track_count": str(row[2])
                }
                for row in rows
            ]

            conn.close()

        except Exception as e:
            print(f"Error loading recent playlists: {e}")
            self.recent_playlists = []

    def refresh_stats(self):
        self.load_library_stats()
        self.load_recent_playlists()
