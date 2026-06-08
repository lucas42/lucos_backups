"""
Tests for the refresh endpoint cooldown rate-limiting in server.py.

Verifies that POST /refresh-tracking and POST /refresh-config return HTTP 429
with a Retry-After header when called within MIN_REFRESH_INTERVAL_SECONDS of a
previous successful refresh, and return HTTP 303 otherwise.
"""
import datetime
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def server_module():
    """Import server.py with all external dependencies stubbed."""
    os.environ.setdefault("PORT", "9999")
    sys.modules.pop("server", None)

    TrackingNotReadyError = type("TrackingNotReadyError", (Exception,), {})
    tracking_stub = MagicMock()
    tracking_stub.TrackingNotReadyError = TrackingNotReadyError

    stubs = {
        "utils.tracking": tracking_stub,
        "utils.auth": MagicMock(),
        "utils.config": MagicMock(),
        "jinja2": MagicMock(),
        "schedule_tracker": MagicMock(),
        "waitress": MagicMock(),
    }
    fake_env = MagicMock()
    stubs["jinja2"].Environment.return_value = fake_env
    stubs["jinja2"].FileSystemLoader = MagicMock()
    stubs["jinja2"].select_autoescape = MagicMock(return_value=MagicMock())

    with patch.dict("sys.modules", stubs):
        import importlib
        import server
        importlib.reload(server)

    yield server

    sys.modules.pop("server", None)


def _make_handler(server_module, method="POST"):
    """Create a BackupsHandler instance with mocked HTTP transport."""
    handler = object.__new__(server_module.BackupsHandler)
    handler.method = method
    handler._response_code = None
    handler._headers = {}

    def _send_response(code):
        handler._response_code = code

    def _send_header(key, val):
        handler._headers[key] = val

    handler.send_response = _send_response
    handler.send_header = _send_header
    handler.end_headers = MagicMock()
    handler.wfile = MagicMock()
    return handler


class TestRefreshTrackingCooldown:

    def test_no_previous_data_allows_refresh(self, server_module):
        """When no tracking data exists yet, the refresh should proceed (allow first run)."""
        handler = _make_handler(server_module)
        with patch.object(server_module, "getAllInfo", side_effect=server_module.TrackingNotReadyError), \
             patch.object(server_module, "fetchAllInfo") as mock_fetch:
            handler.refreshTrackingController()

        assert handler._response_code == 303
        mock_fetch.assert_called_once()

    def test_stale_tracking_data_allows_refresh(self, server_module):
        """When the last tracking run was older than the cooldown window, allow the refresh."""
        now = datetime.datetime.now(datetime.timezone.utc)
        old_time = now - datetime.timedelta(seconds=120)
        handler = _make_handler(server_module)

        with patch.object(server_module, "getAllInfo", return_value={"update_time": old_time}), \
             patch.object(server_module, "fetchAllInfo") as mock_fetch:
            handler.refreshTrackingController()

        assert handler._response_code == 303
        mock_fetch.assert_called_once()

    def test_recent_tracking_throttles_with_429(self, server_module):
        """When tracking ran within the cooldown window, return 429 and skip SSH fan-out."""
        now = datetime.datetime.now(datetime.timezone.utc)
        recent_time = now - datetime.timedelta(seconds=30)
        handler = _make_handler(server_module)

        with patch.object(server_module, "getAllInfo", return_value={"update_time": recent_time}), \
             patch.object(server_module, "fetchAllInfo") as mock_fetch:
            handler.refreshTrackingController()

        assert handler._response_code == 429
        assert "Retry-After" in handler._headers
        mock_fetch.assert_not_called()

    def test_tracking_retry_after_value_is_positive_and_correct(self, server_module):
        """Retry-After should be the remaining seconds until the cooldown expires."""
        now = datetime.datetime.now(datetime.timezone.utc)
        recent_time = now - datetime.timedelta(seconds=30)
        handler = _make_handler(server_module)

        with patch.object(server_module, "getAllInfo", return_value={"update_time": recent_time}), \
             patch.object(server_module, "fetchAllInfo"):
            handler.refreshTrackingController()

        retry_after = int(handler._headers["Retry-After"])
        # 60 - 30 = 30 seconds remaining (allow a small margin for test execution time)
        assert 25 <= retry_after <= 30

    def test_wrong_method_returns_405(self, server_module):
        """GET /refresh-tracking should return 405 without checking the cooldown."""
        handler = _make_handler(server_module, method="GET")

        with patch.object(server_module, "getAllInfo") as mock_get, \
             patch.object(server_module, "fetchAllInfo") as mock_fetch:
            handler.refreshTrackingController()

        assert handler._response_code == 405
        mock_get.assert_not_called()
        mock_fetch.assert_not_called()


class TestRefreshConfigCooldown:

    def test_no_previous_config_refresh_allows(self, server_module):
        """When there has been no previous config refresh, the request should proceed."""
        server_module._last_config_refresh = None
        handler = _make_handler(server_module)

        with patch.object(server_module, "fetchConfig") as mock_fetch:
            handler.refreshConfigController()

        assert handler._response_code == 303
        mock_fetch.assert_called_once()

    def test_stale_config_refresh_allows(self, server_module):
        """When the last config refresh was older than the cooldown window, allow the refresh."""
        now = datetime.datetime.now(datetime.timezone.utc)
        server_module._last_config_refresh = now - datetime.timedelta(seconds=120)
        handler = _make_handler(server_module)

        with patch.object(server_module, "fetchConfig") as mock_fetch:
            handler.refreshConfigController()

        assert handler._response_code == 303
        mock_fetch.assert_called_once()

    def test_recent_config_refresh_throttles_with_429(self, server_module):
        """When config was refreshed within the cooldown window, return 429."""
        now = datetime.datetime.now(datetime.timezone.utc)
        server_module._last_config_refresh = now - datetime.timedelta(seconds=30)
        handler = _make_handler(server_module)

        with patch.object(server_module, "fetchConfig") as mock_fetch:
            handler.refreshConfigController()

        assert handler._response_code == 429
        assert "Retry-After" in handler._headers
        mock_fetch.assert_not_called()

    def test_config_retry_after_value_is_correct(self, server_module):
        """Retry-After should be the remaining seconds until the cooldown expires."""
        now = datetime.datetime.now(datetime.timezone.utc)
        server_module._last_config_refresh = now - datetime.timedelta(seconds=30)
        handler = _make_handler(server_module)

        with patch.object(server_module, "fetchConfig"):
            handler.refreshConfigController()

        retry_after = int(handler._headers["Retry-After"])
        assert 25 <= retry_after <= 30

    def test_successful_config_refresh_updates_cooldown_timestamp(self, server_module):
        """After a successful config refresh, _last_config_refresh should be updated."""
        server_module._last_config_refresh = None
        handler = _make_handler(server_module)

        with patch.object(server_module, "fetchConfig"):
            handler.refreshConfigController()

        assert server_module._last_config_refresh is not None

    def test_failed_config_refresh_does_not_update_cooldown_timestamp(self, server_module):
        """If fetchConfig() raises, _last_config_refresh should not be updated."""
        server_module._last_config_refresh = None
        handler = _make_handler(server_module)

        with patch.object(server_module, "fetchConfig", side_effect=Exception("timeout")):
            handler.refreshConfigController()

        assert handler._response_code == 500
        assert server_module._last_config_refresh is None

    def test_wrong_method_returns_405(self, server_module):
        """GET /refresh-config should return 405 without checking the cooldown."""
        now = datetime.datetime.now(datetime.timezone.utc)
        server_module._last_config_refresh = now - datetime.timedelta(seconds=1)  # Very recent
        handler = _make_handler(server_module, method="GET")

        with patch.object(server_module, "fetchConfig") as mock_fetch:
            handler.refreshConfigController()

        assert handler._response_code == 405
        mock_fetch.assert_not_called()
