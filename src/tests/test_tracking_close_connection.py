"""
Regression tests for the fd-leak fix in utils.tracking.fetchAllInfo.

Before the fix, fetchAllInfo() never called host.closeConnection(), leaking
one paramiko transport socket per host per run.  These tests assert that
closeConnection() is called for every host regardless of whether getData()
succeeds or raises — covering both the happy path and the error path.
"""
import sys
import pytest
from unittest.mock import patch, MagicMock, call


def _make_host(name="avalon", fail=False):
    host = MagicMock()
    host.name = name
    host.domain = "{}.s.l42.eu".format(name)
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


@pytest.fixture(scope="module")
def tracking():
    """Import utils.tracking with all network deps stubbed so the module-level
    fetchAllInfo() background thread doesn't attempt real connections."""
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


class TestCloseConnectionCalled:

    def _run_fetch(self, tracking, hosts):
        tracking._retry_timer = None
        with patch.object(tracking, "Host") as mock_h, \
             patch.object(tracking, "Repository") as mock_r, \
             patch.object(tracking, "Volume") as mock_v, \
             patch.object(tracking, "updateScheduleTracker"):
            mock_h.getAll.return_value = hosts
            mock_r.getAll.return_value = []
            mock_v.getMissing.return_value = []
            tracking.fetchAllInfo()
        return hosts

    def test_close_connection_called_when_host_succeeds(self, tracking):
        """closeConnection() must be called even when getData() returns normally."""
        host = _make_host(name="avalon", fail=False)
        self._run_fetch(tracking, [host])
        host.closeConnection.assert_called_once()

    def test_close_connection_called_when_host_fails(self, tracking):
        """closeConnection() must be called even when getData() raises."""
        host = _make_host(name="avalon", fail=True)
        self._run_fetch(tracking, [host])
        host.closeConnection.assert_called_once()

    def test_close_connection_called_for_all_hosts(self, tracking):
        """closeConnection() must be called for every host, not just the first."""
        hosts = [
            _make_host(name="avalon", fail=False),
            _make_host(name="xwing", fail=False),
            _make_host(name="salvare", fail=True),
        ]
        self._run_fetch(tracking, hosts)
        for host in hosts:
            host.closeConnection.assert_called_once()

    def test_close_connection_called_when_first_host_fails_rest_succeed(self, tracking):
        """A failing first host must not prevent closeConnection() on later hosts."""
        hosts = [
            _make_host(name="avalon", fail=True),
            _make_host(name="xwing", fail=False),
        ]
        self._run_fetch(tracking, hosts)
        for host in hosts:
            host.closeConnection.assert_called_once()
