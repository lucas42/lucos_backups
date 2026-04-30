"""
Tests for scripts/create-backups.py — skip-if-fresh, lockfile, and
schedule-tracker integration.

The module is imported with all external dependencies (loganne,
schedule_tracker, classes.host, classes.repository) mocked via sys.modules
so that importing it does NOT trigger a live backup run.

Because the run() function calls sys.exit(0) on the no-op paths, tests that
exercise those paths must catch SystemExit.
"""
import fcntl
import importlib
import os
import sys
import time
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _stub_modules():
    """Inject MagicMock stubs for all external imports in create-backups.py."""
    stubs = {
        "loganne": MagicMock(),
        "schedule_tracker": MagicMock(),
        "classes.host": MagicMock(),
        "classes.repository": MagicMock(),
    }
    return stubs


def _import_create_backups(stubs):
    """(Re-)import scripts.create-backups with the given stubs active."""
    # Pop any cached version so we get a clean module
    sys.modules.pop("scripts.create-backups", None)
    sys.modules.pop("scripts", None)
    with patch.dict("sys.modules", stubs):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "scripts.create-backups",
            os.path.join(os.path.dirname(__file__), "..", "scripts", "create-backups.py"),
        )
        module = importlib.util.module_from_spec(spec)
        # Execute the module — this only defines run() and sets constants;
        # the __name__ guard prevents run() from being called automatically.
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def cb(tmp_path):
    """Import create-backups with stubs and return (module, stubs, paths)."""
    stubs = _stub_modules()
    with patch.dict("sys.modules", stubs):
        module = _import_create_backups(stubs)
    lock_file = str(tmp_path / "create.lock")
    last_success_file = str(tmp_path / "last_success")
    return module, stubs, lock_file, last_success_file


# ---------------------------------------------------------------------------
# No-op: skip-if-fresh
# ---------------------------------------------------------------------------

class TestSkipIfFresh:

    def test_fresh_marker_causes_noop(self, cb, tmp_path):
        """When last_success exists and is recent, run() exits without running backups."""
        module, stubs, lock_file, last_success_file = cb
        # Create a fresh marker (just now)
        open(last_success_file, 'w').close()

        with pytest.raises(SystemExit) as exc_info:
            module.run(
                lock_file=lock_file,
                last_success_file=last_success_file,
                fresh_threshold_seconds=72000,  # 20 hours
            )

        assert exc_info.value.code == 0, "No-op should exit 0"
        # Backup hosts must not have been iterated
        stubs["classes.host"].Host.getAll.assert_not_called()
        stubs["classes.repository"].Repository.getAll.assert_not_called()

    def test_fresh_marker_emits_success_tick(self, cb, tmp_path):
        """The no-op path must call updateScheduleTracker(success=True, ...) so
        schedule-tracker stays green — the detection improvement evaporates if
        the no-op silently skips the tracker update."""
        module, stubs, lock_file, last_success_file = cb
        open(last_success_file, 'w').close()
        mock_tracker = stubs["schedule_tracker"].updateScheduleTracker

        with pytest.raises(SystemExit):
            module.run(
                lock_file=lock_file,
                last_success_file=last_success_file,
                fresh_threshold_seconds=72000,
            )

        mock_tracker.assert_called_once()
        call_kwargs = mock_tracker.call_args
        assert call_kwargs.kwargs.get("success") is True or (
            len(call_kwargs.args) > 0 and call_kwargs.args[0] is True
        ), "updateScheduleTracker must be called with success=True on no-op"

    def test_fresh_marker_success_tick_includes_message(self, cb, tmp_path):
        """The no-op tracker call should include a descriptive message."""
        module, stubs, lock_file, last_success_file = cb
        open(last_success_file, 'w').close()
        mock_tracker = stubs["schedule_tracker"].updateScheduleTracker

        with pytest.raises(SystemExit):
            module.run(
                lock_file=lock_file,
                last_success_file=last_success_file,
                fresh_threshold_seconds=72000,
            )

        call_kwargs = mock_tracker.call_args
        message = call_kwargs.kwargs.get("message", "")
        assert message, "No-op tracker call should include a non-empty message"

    def test_stale_marker_triggers_full_run(self, cb, tmp_path):
        """When last_success exists but is older than the threshold, run() proceeds
        with the full backup logic (stale = needs re-run)."""
        module, stubs, lock_file, last_success_file = cb
        open(last_success_file, 'w').close()

        # Make the file appear to be 25 hours old by patching time.time
        fake_now = os.path.getmtime(last_success_file) + 25 * 3600
        mock_host = MagicMock()
        mock_host.getVolumes.return_value = []
        mock_host.getOneOffFiles.return_value = []
        stubs["classes.host"].Host.getAll.return_value = [mock_host]
        stubs["classes.repository"].Repository.getAll.return_value = []

        with patch("time.time", return_value=fake_now):
            module.run(
                lock_file=lock_file,
                last_success_file=last_success_file,
                fresh_threshold_seconds=72000,
            )

        stubs["classes.host"].Host.getAll.assert_called_once()

    def test_missing_marker_triggers_full_run(self, cb, tmp_path):
        """When last_success does not exist (first run or container restart),
        run() proceeds with the full backup logic."""
        module, stubs, lock_file, last_success_file = cb
        # No last_success file created — simulates first run
        mock_host = MagicMock()
        mock_host.getVolumes.return_value = []
        mock_host.getOneOffFiles.return_value = []
        stubs["classes.host"].Host.getAll.return_value = [mock_host]
        stubs["classes.repository"].Repository.getAll.return_value = []

        module.run(
            lock_file=lock_file,
            last_success_file=last_success_file,
            fresh_threshold_seconds=72000,
        )

        stubs["classes.host"].Host.getAll.assert_called_once()


# ---------------------------------------------------------------------------
# No-op: concurrent-run lock contention
# ---------------------------------------------------------------------------

class TestLockContention:

    def test_lock_contention_causes_noop(self, cb, tmp_path):
        """When the lock cannot be acquired (previous run in flight), run() exits 0."""
        module, stubs, lock_file, last_success_file = cb

        with patch("fcntl.flock", side_effect=BlockingIOError):
            with pytest.raises(SystemExit) as exc_info:
                module.run(
                    lock_file=lock_file,
                    last_success_file=last_success_file,
                    fresh_threshold_seconds=72000,
                )

        assert exc_info.value.code == 0
        stubs["classes.host"].Host.getAll.assert_not_called()

    def test_lock_contention_emits_success_tick(self, cb, tmp_path):
        """Lock contention must emit updateScheduleTracker(success=True) so the
        in-flight run's eventual success tick is not the only signal."""
        module, stubs, lock_file, last_success_file = cb
        mock_tracker = stubs["schedule_tracker"].updateScheduleTracker

        with patch("fcntl.flock", side_effect=BlockingIOError):
            with pytest.raises(SystemExit):
                module.run(
                    lock_file=lock_file,
                    last_success_file=last_success_file,
                    fresh_threshold_seconds=72000,
                )

        mock_tracker.assert_called_once()
        call_kwargs = mock_tracker.call_args
        assert call_kwargs.kwargs.get("success") is True


# ---------------------------------------------------------------------------
# Full run: marker written and tracker called on success
# ---------------------------------------------------------------------------

class TestFullRun:

    def _setup_hosts(self, stubs):
        mock_host = MagicMock()
        mock_host.getVolumes.return_value = []
        mock_host.getOneOffFiles.return_value = []
        stubs["classes.host"].Host.getAll.return_value = [mock_host]
        stubs["classes.repository"].Repository.getAll.return_value = []
        return mock_host

    def test_success_writes_last_success_marker(self, cb, tmp_path):
        """After a successful run with no failures, the last_success marker is written."""
        module, stubs, lock_file, last_success_file = cb
        self._setup_hosts(stubs)

        module.run(
            lock_file=lock_file,
            last_success_file=last_success_file,
            fresh_threshold_seconds=72000,
        )

        assert os.path.exists(last_success_file), (
            "last_success marker must be written after a successful run "
            "so the next cron run can skip if the backup is fresh"
        )

    def test_success_emits_success_tracker_tick(self, cb, tmp_path):
        """A successful run must call updateScheduleTracker(success=True)."""
        module, stubs, lock_file, last_success_file = cb
        self._setup_hosts(stubs)
        mock_tracker = stubs["schedule_tracker"].updateScheduleTracker

        module.run(
            lock_file=lock_file,
            last_success_file=last_success_file,
            fresh_threshold_seconds=72000,
        )

        # Find the success=True call (there should be exactly one from the full run)
        success_calls = [c for c in mock_tracker.call_args_list
                         if c.kwargs.get("success") is True]
        assert success_calls, "updateScheduleTracker(success=True) must be called on a clean run"

    def test_failure_emits_failure_tracker_tick(self, cb, tmp_path):
        """When a backup fails, updateScheduleTracker(success=False, message=...) is called."""
        module, stubs, lock_file, last_success_file = cb

        # Simulate one volume backup failing
        mock_volume = MagicMock()
        mock_volume.name = "lucos_photos_photos"
        mock_volume.backup.side_effect = Exception("SSH timeout")
        mock_host = MagicMock()
        mock_host.domain = "avalon.s.l42.eu"
        mock_host.getVolumes.return_value = [mock_volume]
        mock_host.getOneOffFiles.return_value = []
        stubs["classes.host"].Host.getAll.return_value = [mock_host]
        stubs["classes.repository"].Repository.getAll.return_value = []
        mock_tracker = stubs["schedule_tracker"].updateScheduleTracker

        module.run(
            lock_file=lock_file,
            last_success_file=last_success_file,
            fresh_threshold_seconds=72000,
        )

        failure_calls = [c for c in mock_tracker.call_args_list
                         if c.kwargs.get("success") is False]
        assert failure_calls, "updateScheduleTracker(success=False) must be called when a backup fails"
        assert "message" in failure_calls[0].kwargs, "failure tracker call must include a message"

    def test_failure_does_not_write_last_success_marker(self, cb, tmp_path):
        """When a backup fails, the last_success marker must NOT be written —
        writing it would cause the next cron run to skip when it should retry."""
        module, stubs, lock_file, last_success_file = cb

        mock_volume = MagicMock()
        mock_volume.name = "lucos_photos_photos"
        mock_volume.backup.side_effect = Exception("disk full")
        mock_host = MagicMock()
        mock_host.domain = "avalon.s.l42.eu"
        mock_host.getVolumes.return_value = [mock_volume]
        mock_host.getOneOffFiles.return_value = []
        stubs["classes.host"].Host.getAll.return_value = [mock_host]
        stubs["classes.repository"].Repository.getAll.return_value = []

        module.run(
            lock_file=lock_file,
            last_success_file=last_success_file,
            fresh_threshold_seconds=72000,
        )

        assert not os.path.exists(last_success_file), (
            "last_success marker must NOT be written when backups fail — "
            "the next cron run must retry rather than skip"
        )
