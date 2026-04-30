"""
Tests for the backupsWithoutOriginals detection in utils.tracking.fetchAllInfo.

Verifies that backups whose source volume no longer exists on the host are
flagged correctly, and that volumes from failed-tracking hosts are not
false-positived.
"""
import sys
import pytest
from unittest.mock import patch, MagicMock


def _make_host(name="avalon", domain="avalon.s.l42.eu", fail=False):
    host = MagicMock()
    host.name = name
    host.domain = domain
    if fail:
        host.getData.side_effect = Exception("SSH timeout")
    else:
        host.getData.return_value = {
            "volumes": [],
            "one_off_files": [],
            "backups": [],
            "disk": {"used_percentage": 40},
        }
    return host


def _make_volume_data(name, source_host="avalon"):
    return {"name": name, "source_host": source_host, "known": True}


def _make_backup_data(name, source_host="avalon", backup_type="volume"):
    return {"name": name, "source_host": source_host, "type": backup_type}


@pytest.fixture(scope="module")
def tracking():
    """Import utils.tracking with all network deps mocked."""
    sys.modules.pop("utils.tracking", None)

    mock_host_cls = MagicMock()
    mock_host_cls.getAll.return_value = []
    mock_volume_cls = MagicMock()
    mock_volume_cls.getMissing.return_value = []
    mock_repo_cls = MagicMock()
    mock_repo_cls.getAll.return_value = []

    fake_modules = {
        "schedule_tracker": MagicMock(),
        "classes.host": MagicMock(Host=mock_host_cls),
        "classes.volume": MagicMock(Volume=mock_volume_cls),
        "classes.repository": MagicMock(Repository=mock_repo_cls),
        "utils.config": MagicMock(),
        "fabric": MagicMock(),
        "invoke": MagicMock(),
    }

    with patch.dict("sys.modules", fake_modules):
        import utils.tracking as t
        yield t

    if t._retry_timer is not None:
        t._retry_timer.cancel()
        t._retry_timer = None


def run_fetch(tracking, hosts):
    """Helper: run fetchAllInfo with the given host mocks and return latestInfo."""
    tracking._retry_timer = None
    with patch.object(tracking, "Host") as mock_h, \
         patch.object(tracking, "Repository") as mock_r, \
         patch.object(tracking, "Volume") as mock_v, \
         patch.object(tracking, "updateScheduleTracker"):
        mock_h.getAll.return_value = hosts
        mock_r.getAll.return_value = []
        mock_v.getMissing.return_value = []
        tracking.fetchAllInfo()
    return tracking.latestInfo


class TestBackupsWithoutOriginals:

    def test_no_backups_no_volumes(self, tracking):
        """With no backups and no volumes, result should be empty."""
        host = _make_host()
        info = run_fetch(tracking, [host])
        assert info["backupsWithoutOriginals"] == []

    def test_backup_matches_live_volume(self, tracking):
        """A backup whose source volume is still live should not be flagged."""
        host = _make_host()
        host.getData.return_value = {
            "volumes": [_make_volume_data("lucos_photos_postgres_data")],
            "one_off_files": [],
            "backups": [_make_backup_data("lucos_photos_postgres_data")],
            "disk": {"used_percentage": 40},
        }
        info = run_fetch(tracking, [host])
        assert info["backupsWithoutOriginals"] == []

    def test_backup_without_matching_volume_is_flagged(self, tracking):
        """A volume backup with no matching live volume should be flagged."""
        host = _make_host()
        host.getData.return_value = {
            "volumes": [],  # volume is gone
            "one_off_files": [],
            "backups": [_make_backup_data("lucos_photos_postgres_data")],
            "disk": {"used_percentage": 40},
        }
        info = run_fetch(tracking, [host])
        assert "avalon/lucos_photos_postgres_data" in info["backupsWithoutOriginals"]

    def test_non_volume_backup_type_not_flagged(self, tracking):
        """Backups of type 'repository' or 'one-off' should not be checked."""
        host = _make_host()
        host.getData.return_value = {
            "volumes": [],
            "one_off_files": [],
            "backups": [_make_backup_data("some_repo", backup_type="repository")],
            "disk": {"used_percentage": 40},
        }
        info = run_fetch(tracking, [host])
        assert info["backupsWithoutOriginals"] == []

    def test_failed_host_volumes_not_flagged(self, tracking):
        """Backups from a host that failed tracking should be excluded."""
        failing_host = _make_host(name="avalon", fail=True)
        # A storage host that holds a backup copy from the failing host
        storage_host = _make_host(name="xwing", domain="xwing.s.l42.eu")
        storage_host.getData.return_value = {
            "volumes": [],
            "one_off_files": [],
            "backups": [_make_backup_data("lucos_photos_postgres_data", source_host="avalon")],
            "disk": {"used_percentage": 50},
        }
        info = run_fetch(tracking, [failing_host, storage_host])
        # avalon failed tracking — we don't know if its volumes exist, so don't flag
        assert info["backupsWithoutOriginals"] == []

    def test_duplicate_backup_copies_deduplicated(self, tracking):
        """Multiple backup copies of the same volume should produce only one entry."""
        host = _make_host()
        host.getData.return_value = {
            "volumes": [],
            "one_off_files": [],
            "backups": [
                _make_backup_data("lucos_photos_postgres_data"),
                _make_backup_data("lucos_photos_postgres_data"),  # duplicate
            ],
            "disk": {"used_percentage": 40},
        }
        info = run_fetch(tracking, [host])
        assert info["backupsWithoutOriginals"].count("avalon/lucos_photos_postgres_data") == 1

    def test_multiple_missing_volumes_all_flagged(self, tracking):
        """Multiple backed-up volumes that are all missing should all be flagged."""
        host = _make_host()
        host.getData.return_value = {
            "volumes": [],
            "one_off_files": [],
            "backups": [
                _make_backup_data("lucos_photos_postgres_data"),
                _make_backup_data("lucos_contacts_db_data"),
            ],
            "disk": {"used_percentage": 40},
        }
        info = run_fetch(tracking, [host])
        assert "avalon/lucos_photos_postgres_data" in info["backupsWithoutOriginals"]
        assert "avalon/lucos_contacts_db_data" in info["backupsWithoutOriginals"]

    def test_cross_host_backup_without_original_is_flagged(self, tracking):
        """A cross-host backup copy for a volume gone from its source host should be flagged."""
        # xwing holds a copy of avalon's volume, but avalon no longer has that volume
        avalon = _make_host(name="avalon")
        avalon.getData.return_value = {
            "volumes": [],  # volume gone from avalon
            "one_off_files": [],
            "backups": [],
            "disk": {"used_percentage": 40},
        }
        xwing = _make_host(name="xwing", domain="xwing.s.l42.eu")
        xwing.getData.return_value = {
            "volumes": [],
            "one_off_files": [],
            "backups": [_make_backup_data("lucos_photos_postgres_data", source_host="avalon")],
            "disk": {"used_percentage": 50},
        }
        info = run_fetch(tracking, [avalon, xwing])
        assert "avalon/lucos_photos_postgres_data" in info["backupsWithoutOriginals"]
