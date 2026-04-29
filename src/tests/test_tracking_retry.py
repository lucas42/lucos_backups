"""
Tests for the retry logic in utils.tracking.fetchAllInfo.

The module starts fetchAllInfo() in a background daemon thread at import time,
so we must mock all network dependencies before importing it.  The `tracking`
fixture handles this once per module; individual tests reset mutable state
(_retry_timer) before each call.
"""
import sys
import pytest
from unittest.mock import patch, MagicMock


def _make_host(name="avalon", fail=False):
    host = MagicMock()
    host.name = name
    host.domain = "avalon.s.l42.eu"
    if fail:
        host.getData.side_effect = Exception("Command did not complete within 10 seconds")
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
    """Import utils.tracking with all network deps mocked so the module-level
    fetchAllInfo() call doesn't attempt real SSH connections.

    Several modules (schedule_tracker, classes.repository, utils.config) call
    sys.exit() at import time when required env vars are absent.  We inject
    MagicMocks for all of them into sys.modules before importing tracking.
    """
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


def test_retry_timer_scheduled_when_host_fails(tracking):
    """fetchAllInfo should schedule a retry timer when at least one host fails."""
    tracking._retry_timer = None

    with patch.object(tracking, "Host") as mock_h, \
         patch.object(tracking, "Repository") as mock_r, \
         patch.object(tracking, "Volume") as mock_v, \
         patch.object(tracking, "updateScheduleTracker"), \
         patch("utils.tracking.threading.Timer") as mock_timer:
        mock_h.getAll.return_value = [_make_host(fail=True)]
        mock_r.getAll.return_value = []
        mock_v.getMissing.return_value = []
        timer_instance = MagicMock()
        mock_timer.return_value = timer_instance

        tracking.fetchAllInfo()

    mock_timer.assert_called_once_with(tracking.RETRY_DELAY_SECONDS, tracking.fetchAllInfo)
    assert timer_instance.daemon is True
    timer_instance.start.assert_called_once()


def test_pending_timer_cancelled_on_next_run(tracking):
    """A pending retry timer should be cancelled when fetchAllInfo runs again."""
    existing_timer = MagicMock()
    tracking._retry_timer = existing_timer

    with patch.object(tracking, "Host") as mock_h, \
         patch.object(tracking, "Repository") as mock_r, \
         patch.object(tracking, "Volume") as mock_v, \
         patch.object(tracking, "updateScheduleTracker"), \
         patch("utils.tracking.threading.Timer") as mock_timer:
        mock_h.getAll.return_value = [_make_host(fail=False)]
        mock_r.getAll.return_value = []
        mock_v.getMissing.return_value = []
        mock_timer.return_value = MagicMock()

        tracking.fetchAllInfo()

    # Existing timer must have been cancelled at the start of the run
    existing_timer.cancel.assert_called_once()
    # All hosts succeeded — no new timer should be scheduled
    mock_timer.assert_not_called()
