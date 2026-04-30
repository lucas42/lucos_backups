"""
Tests for the backup-without-original debug message format in server.py.

The format_backup_without_original helper is extracted to make this logic
testable without starting an HTTP server.
"""
import os
import sys
import pytest
from unittest.mock import MagicMock


@pytest.fixture(scope="module")
def server_module():
    """Import server.py with all external dependencies stubbed."""
    os.environ.setdefault("PORT", "9999")

    stubs = {
        "utils.tracking": MagicMock(),
        "utils.auth": MagicMock(),
        "utils.config": MagicMock(),
        "jinja2": MagicMock(),
        "schedule_tracker": MagicMock(),
        "waitress": MagicMock(),
    }
    # Stub jinja2.Environment so the module-level templateEnv assignment works
    fake_env = MagicMock()
    fake_env.return_value = fake_env
    stubs["jinja2"].Environment.return_value = fake_env
    stubs["jinja2"].FileSystemLoader = MagicMock()
    stubs["jinja2"].select_autoescape = MagicMock(return_value=MagicMock())

    with __import__("unittest.mock", fromlist=["patch"]).patch.dict("sys.modules", stubs):
        import importlib
        import server
        importlib.reload(server)

    yield server

    # Clean up
    sys.modules.pop("server", None)


class TestFormatBackupWithoutOriginal:
    """Tests for the format_backup_without_original helper."""

    def test_single_storage_host(self, server_module):
        """When only one host holds copies, it appears after 'copies on:'."""
        backups = [
            {"type": "volume", "source_host": "avalon", "name": "lucos_contacts_staticfiles", "stored_host": "aurora"},
        ]
        result = server_module.format_backup_without_original(
            "avalon/lucos_contacts_staticfiles", backups
        )
        assert result == "avalon/lucos_contacts_staticfiles (copies on: aurora)"

    def test_multiple_storage_hosts_sorted(self, server_module):
        """Multiple storage hosts appear sorted alphabetically."""
        backups = [
            {"type": "volume", "source_host": "avalon", "name": "lucos_photos_postgres_data", "stored_host": "xwing"},
            {"type": "volume", "source_host": "avalon", "name": "lucos_photos_postgres_data", "stored_host": "aurora"},
            {"type": "volume", "source_host": "avalon", "name": "lucos_photos_postgres_data", "stored_host": "salvare"},
            {"type": "volume", "source_host": "avalon", "name": "lucos_photos_postgres_data", "stored_host": "avalon"},
        ]
        result = server_module.format_backup_without_original(
            "avalon/lucos_photos_postgres_data", backups
        )
        assert result == "avalon/lucos_photos_postgres_data (copies on: aurora, avalon, salvare, xwing)"

    def test_only_volume_backups_counted(self, server_module):
        """Repository and one-off backups with the same name must not be counted."""
        backups = [
            {"type": "volume", "source_host": "avalon", "name": "lucos_notes_stateFile", "stored_host": "salvare"},
            {"type": "repository", "source_host": "avalon", "name": "lucos_notes_stateFile", "stored_host": "aurora"},
            {"type": "one-off", "source_host": "avalon", "name": "lucos_notes_stateFile", "stored_host": "xwing"},
        ]
        result = server_module.format_backup_without_original(
            "avalon/lucos_notes_stateFile", backups
        )
        # Only 'salvare' — the type=volume entry
        assert result == "avalon/lucos_notes_stateFile (copies on: salvare)"

    def test_deduplicates_storage_hosts(self, server_module):
        """Multiple backup instances from the same storage host count once."""
        backups = [
            {"type": "volume", "source_host": "avalon", "name": "lucos_photos_postgres_data", "stored_host": "aurora"},
            {"type": "volume", "source_host": "avalon", "name": "lucos_photos_postgres_data", "stored_host": "aurora"},
        ]
        result = server_module.format_backup_without_original(
            "avalon/lucos_photos_postgres_data", backups
        )
        assert result == "avalon/lucos_photos_postgres_data (copies on: aurora)"

    def test_unrelated_backup_not_included(self, server_module):
        """Backups for a different volume name must not pollute the result."""
        backups = [
            {"type": "volume", "source_host": "avalon", "name": "lucos_photos_postgres_data", "stored_host": "aurora"},
            {"type": "volume", "source_host": "avalon", "name": "lucos_contacts_db_data", "stored_host": "salvare"},
        ]
        result = server_module.format_backup_without_original(
            "avalon/lucos_photos_postgres_data", backups
        )
        assert "salvare" not in result
        assert result == "avalon/lucos_photos_postgres_data (copies on: aurora)"

    def test_different_source_host_not_included(self, server_module):
        """Backup copies for the same volume name but different source host must not be included."""
        backups = [
            {"type": "volume", "source_host": "salvare", "name": "lucos_photos_postgres_data", "stored_host": "aurora"},
        ]
        result = server_module.format_backup_without_original(
            "avalon/lucos_photos_postgres_data", backups
        )
        # salvare's copy is not for avalon's volume
        assert "aurora" not in result
        assert result == "avalon/lucos_photos_postgres_data (copies on: )"
