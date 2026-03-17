"""
Unit tests for Volume.__init__

These tests verify that Volume correctly parses Docker volume JSON and
raises a clear exception when Docker Compose labels are missing or invalid.

Tests run from src/ so that effort_labels.yaml is accessible at module load.
getVolumesConfig is patched to avoid requiring a live configy connection.
"""
import json
import sys
import pytest
from unittest.mock import MagicMock, patch


def make_raw_json(name, labels=None, mountpoint="/var/lib/docker/volumes/myvolume/_data"):
    """Build the JSON string that Docker's inspect output produces."""
    return json.dumps({
        "Name": name,
        "Mountpoint": mountpoint,
        "Labels": labels,
    })


def make_host(name="avalon"):
    host = MagicMock()
    host.name = name
    return host


# Volumes config returned by the mocked getVolumesConfig — the volume name
# used in happy-path tests must be present so it's treated as "known".
FAKE_VOLUMES_CONFIG = {
    "lucos_photos_photos": {
        "description": "Photo storage",
        "recreate_effort": "huge",
    }
}


class TestVolumeInit:

    def setup_method(self):
        # Inject a fake utils.config module so classes.volume can be imported
        # without triggering live network calls to configy.l42.eu.
        fake_config = MagicMock()
        fake_config.getVolumesConfig = MagicMock(return_value=FAKE_VOLUMES_CONFIG)
        sys.modules.setdefault("utils", MagicMock())
        sys.modules["utils.config"] = fake_config

        # Now import classes.volume — its "from utils.config import ..." will
        # resolve against the injected fake.
        import importlib
        import classes.volume
        importlib.reload(classes.volume)

        # Patch getVolumesConfig on the volume module's own namespace.
        self.patcher = patch("classes.volume.getVolumesConfig", return_value=FAKE_VOLUMES_CONFIG)
        self.patcher.start()

        from classes.volume import Volume
        self.Volume = Volume

    def teardown_method(self):
        self.patcher.stop()
        # Remove the injected modules so other test runs start clean.
        sys.modules.pop("utils.config", None)

    def test_happy_path_known_volume(self):
        """Volume with valid Docker Compose labels populates self.data correctly."""
        labels = "com.docker.compose.project=lucos_photos,com.docker.compose.version=2.1"
        raw = make_raw_json("lucos_photos_photos", labels=labels)
        host = make_host("avalon")

        vol = self.Volume(host, raw)

        assert vol.name == "lucos_photos_photos"
        assert vol.data["name"] == "lucos_photos_photos"
        assert vol.data["known"] is True
        assert vol.data["description"] == "Photo storage"
        assert vol.data["project"]["name"] == "lucos_photos"
        assert vol.data["project"]["link"] == "https://github.com/lucas42/lucos_photos"
        assert vol.data["source_host"] == "avalon"

    def test_happy_path_unknown_volume(self):
        """Volume not in volumes config is marked as unknown but still initialises."""
        labels = "com.docker.compose.project=lucos_contacts"
        raw = make_raw_json("lucos_contacts_db", labels=labels)
        host = make_host("avalon")

        vol = self.Volume(host, raw)

        assert vol.data["known"] is False
        assert vol.data["description"] == "Unknown Volume"
        assert vol.data["project"]["name"] == "lucos_contacts"

    def test_null_labels_raises_clear_exception(self):
        """Null labels (Docker API returns null) raise a clear Exception naming the volume."""
        raw = make_raw_json("lucos_photos_photos", labels=None)
        host = make_host()

        with pytest.raises(Exception) as exc_info:
            self.Volume(host, raw)

        assert "lucos_photos_photos" in str(exc_info.value)

    def test_empty_labels_string_raises_clear_exception(self):
        """Empty string labels raise a clear Exception (not a cryptic unpack error)."""
        raw = make_raw_json("lucos_photos_photos", labels="")
        host = make_host()

        with pytest.raises(Exception) as exc_info:
            self.Volume(host, raw)

        assert "lucos_photos_photos" in str(exc_info.value)

    def test_labels_missing_compose_project_raises_exception(self):
        """Labels present but missing com.docker.compose.project raise a clear Exception."""
        labels = "com.docker.compose.version=2.1,com.docker.compose.service=api"
        raw = make_raw_json("lucos_photos_photos", labels=labels)
        host = make_host()

        with pytest.raises(Exception) as exc_info:
            self.Volume(host, raw)

        assert "lucos_photos_photos" in str(exc_info.value)
