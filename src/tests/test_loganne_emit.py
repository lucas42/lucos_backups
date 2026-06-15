"""Real-transport loganne emit tests for lucos_backups.

Drives the real v2 loganne client against a patched HTTP session so no
actual network call is made.  This locks the wire interface: if a future
change drops `level` from an updateLoganne call-site, the v2 client raises
ValueError before the HTTP call — failing CI rather than passing on a mock.

Tests the prune-backups emit path (triggered when pruneCount > 0 and no
host failures).
"""
import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")


def _load_script(name):
    """Load a script whose filename contains a hyphen via importlib."""
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"),
        os.path.join(_SCRIPTS_DIR, f"{name}.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_host_stub(prune_count=1):
    """Return a mock host whose single backup prunes prune_count instances."""
    backup = MagicMock()
    backup.prune.return_value = prune_count

    host = MagicMock()
    host.domain = "test.example.com"
    host.getBackups.return_value = [backup]
    host.pruneStaleSnapshotPartials.return_value = 0
    host.closeConnection.return_value = None
    return host


# ---------------------------------------------------------------------------
# Real-transport test: prune-backups emit path
# ---------------------------------------------------------------------------

class TestPruneBackupsLoganneEmit:
    """Drive the real loganne v2 client with a stubbed HTTP session."""

    def test_prune_emit_includes_level(self):
        """prune-backups.run() POSTs level='routine' in the HTTP payload."""
        # Import the real loganne module (so v2 validation is active)
        os.environ.setdefault("SYSTEM", "lucos_backups")
        os.environ.setdefault("LOGANNE_ENDPOINT", "http://stub-loganne/events")

        import loganne as _real_loganne

        captured = []

        def _fake_post(url, **kwargs):
            captured.append({"url": url, "json": kwargs.get("json", {})})
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            return resp

        # Stubs for everything except loganne
        host_stub = _make_host_stub(prune_count=1)
        stubs = {
            "schedule_tracker": MagicMock(),
            "classes.host": MagicMock(),
        }
        stubs["classes.host"].Host.getAll.return_value = [host_stub]

        with patch.dict("sys.modules", stubs), \
             patch.object(_real_loganne.session, "post", side_effect=_fake_post):
            module = _load_script("prune-backups")
            module.run()

        # Verify at least one HTTP POST was made to the loganne endpoint
        prune_calls = [c for c in captured if c["json"].get("type") == "prune-backups"]
        assert len(prune_calls) >= 1, "Expected at least one prune-backups loganne POST"

        payload = prune_calls[0]["json"]
        assert payload.get("level") == "routine", (
            f"Expected level='routine' in HTTP payload, got: {payload}"
        )
        assert payload.get("type") == "prune-backups"
        assert "source" in payload

    def test_prune_invalid_level_raises(self):
        """Sanity-check: the v2 client raises ValueError for an unknown level."""
        os.environ.setdefault("SYSTEM", "lucos_backups")
        os.environ.setdefault("LOGANNE_ENDPOINT", "http://stub-loganne/events")

        import loganne as _real_loganne

        with pytest.raises(ValueError, match="Invalid level"):
            _real_loganne.updateLoganne(
                type="test",
                humanReadable="test",
                level="not-a-valid-level",
            )
