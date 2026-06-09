"""
Unit tests for Host.getVolumes() resilience.

Before the fix, a single Volume.__init__ failure (e.g. from an invalid
recreate_effort value) would propagate up through getVolumes() and abort
getData() for the entire host, dropping all volumes from tracking.

These tests verify that a bad volume is skipped and logged rather than
crashing the entire host's volume retrieval.

Host.__init__ is bypassed via __new__ so the tests don't need a live SSH
connection or a real fabric setup.
"""
import sys
import pytest
from unittest.mock import MagicMock, patch


def _make_host_instance(domain="avalon.s.l42.eu", is_storage_only=False):
    """Build a Host-like object without calling __init__ (avoids fabric/SSH setup)."""
    # Import after stubbing to avoid module-level network calls.
    from classes.host import Host
    host = Host.__new__(Host)
    host.name = "avalon"
    host.domain = domain
    host.is_storage_only = is_storage_only

    # Mock the fabric connection so connection.run() can be controlled per-test.
    host.connection = MagicMock()
    return host


GOOD_RAW = '{"Name":"lucos_photos_photos","Mountpoint":"/var/lib/docker/volumes/lucos_photos_photos/_data","Labels":"com.docker.compose.project=lucos_photos"}'
BAD_RAW  = '{"Name":"lucos_dns_configy-sync-cache","Mountpoint":"/var/lib/docker/volumes/cache/_data","Labels":"com.docker.compose.project=lucos_dns"}'


@pytest.fixture(autouse=True)
def stub_modules():
    """Stub fabric/invoke/utils.config so classes.host can be imported cleanly."""
    stubs = {
        "fabric": MagicMock(),
        "invoke": MagicMock(),
        "utils.config": MagicMock(),
        "utils": MagicMock(),
        "schedule_tracker": MagicMock(),
    }
    with patch.dict("sys.modules", stubs):
        # Force a fresh import so the stub bindings take effect.
        sys.modules.pop("classes.host", None)
        sys.modules.pop("classes.volume", None)
        yield
    sys.modules.pop("classes.host", None)
    sys.modules.pop("classes.volume", None)


class TestGetVolumesIsolation:

    def _make_host(self):
        from classes.host import Host
        host = Host.__new__(Host)
        host.name = "avalon"
        host.domain = "avalon.s.l42.eu"
        host.is_storage_only = False
        host.connection = MagicMock()
        return host

    def test_all_good_volumes_returned(self):
        """All valid volumes are returned when none raise."""
        host = self._make_host()
        host.connection.run.return_value.stdout = GOOD_RAW + "\n" + GOOD_RAW

        good_vol = MagicMock()
        with patch("classes.host.Volume", return_value=good_vol) as MockVolume:
            result = host.getVolumes()

        assert len(result) == 2
        assert result == [good_vol, good_vol]

    def test_bad_volume_is_skipped_not_raised(self):
        """A Volume.__init__ failure on one entry is swallowed; remaining volumes are returned."""
        host = self._make_host()
        # Two raw volume lines: first will fail, second will succeed.
        host.connection.run.return_value.stdout = BAD_RAW + "\n" + GOOD_RAW

        good_vol = MagicMock()

        def volume_side_effect(h, raw):
            if raw == BAD_RAW:
                raise KeyError("low")
            return good_vol

        with patch("classes.host.Volume", side_effect=volume_side_effect):
            result = host.getVolumes()

        # Only the good volume should be in the list.
        assert result == [good_vol]

    def test_bad_volume_logs_error(self, capsys):
        """A Volume.__init__ failure prints an error message to stdout."""
        host = self._make_host()
        host.connection.run.return_value.stdout = BAD_RAW

        with patch("classes.host.Volume", side_effect=KeyError("low")):
            host.getVolumes()

        captured = capsys.readouterr()
        assert "avalon.s.l42.eu" in captured.out

    def test_storage_only_host_returns_empty_list(self):
        """Storage-only hosts return an empty list without touching the SSH connection."""
        host = self._make_host()
        host.is_storage_only = True

        with patch("classes.host.Volume") as MockVolume:
            result = host.getVolumes()

        host.connection.run.assert_not_called()
        MockVolume.assert_not_called()
        assert result == []

    def test_all_bad_volumes_returns_empty_list(self):
        """When every volume fails to parse, an empty list is returned (not an exception)."""
        host = self._make_host()
        host.connection.run.return_value.stdout = BAD_RAW + "\n" + BAD_RAW

        with patch("classes.host.Volume", side_effect=KeyError("low")):
            result = host.getVolumes()

        assert result == []
