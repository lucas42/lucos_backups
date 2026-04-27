"""
Unit tests for Volume.shouldBackup() and Volume.backupToAll()

Tests run from src/ so that effort_labels.yaml is accessible at module load.
getVolumesConfig, getHostsConfig, and Host are patched to avoid network calls.
"""
import json
import sys
import pytest
from unittest.mock import MagicMock, patch, call


FAKE_VOLUMES_CONFIG = {
    "lucos_photos_photos": {
        "description": "Photo storage",
        "recreate_effort": "huge",
        "skip_backup_on_hosts": ["salvare"],
    },
    "lucos_contacts_db": {
        "description": "Contacts database",
        "recreate_effort": "small",
        "skip_backup": True,
    },
    "lucos_notes_data": {
        "description": "Notes data",
        "recreate_effort": "small",
    },
}

FAKE_HOSTS_CONFIG = {
    "avalon":  {"domain": "avalon.l42.eu",   "backup_root": "/srv/backups/"},
    "xwing":   {"domain": "xwing.l42.eu",    "backup_root": "/srv/backups/"},
    "salvare": {"domain": "salvare.l42.eu",  "backup_root": "/srv/backups/"},
}

LABELS = "com.docker.compose.project=lucos_photos"


def make_raw_json(name, labels=LABELS):
    return json.dumps({
        "Name": name,
        "Mountpoint": "/var/lib/docker/volumes/{}/_data".format(name),
        "Labels": labels,
    })


def make_host(name="avalon", domain="avalon.l42.eu"):
    host = MagicMock()
    host.name = name
    host.domain = domain
    host.backup_root = "/srv/backups/"
    return host


def make_target_host(name, domain):
    """Build a mock Host object as returned by Host(hostname) inside backupToAll."""
    h = MagicMock()
    h.name = name
    h.domain = domain
    h.backup_root = "/srv/backups/"
    return h


class TestShouldBackup:

    def setup_method(self):
        fake_config = MagicMock()
        fake_config.getVolumesConfig = MagicMock(return_value=FAKE_VOLUMES_CONFIG)
        fake_config.getHostsConfig = MagicMock(return_value=FAKE_HOSTS_CONFIG)
        sys.modules.setdefault("utils", MagicMock())
        sys.modules["utils.config"] = fake_config

        import importlib
        import classes.volume
        importlib.reload(classes.volume)

        self.vol_patcher = patch("classes.volume.getVolumesConfig", return_value=FAKE_VOLUMES_CONFIG)
        self.hosts_patcher = patch("classes.volume.getHostsConfig", return_value=FAKE_HOSTS_CONFIG)
        self.vol_patcher.start()
        self.hosts_patcher.start()

        from classes.volume import Volume
        self.Volume = Volume

    def teardown_method(self):
        self.vol_patcher.stop()
        self.hosts_patcher.stop()
        sys.modules.pop("utils.config", None)

    def test_should_backup_normal_volume(self):
        """A volume with no skip flags should be backed up."""
        raw = make_raw_json("lucos_notes_data", labels="com.docker.compose.project=lucos_notes")
        vol = self.Volume(make_host("avalon"), raw)
        assert vol.shouldBackup() is True

    def test_should_not_backup_skip_backup_true(self):
        """A volume with skip_backup=True should not be backed up at all."""
        raw = make_raw_json("lucos_contacts_db", labels="com.docker.compose.project=lucos_contacts")
        vol = self.Volume(make_host("avalon"), raw)
        assert vol.shouldBackup() is False

    def test_should_backup_regardless_of_skip_backup_on_hosts(self):
        """skip_backup_on_hosts does not affect shouldBackup — it only filters destinations."""
        raw = make_raw_json("lucos_photos_photos")
        vol = self.Volume(make_host("avalon"), raw)
        assert vol.shouldBackup() is True


class TestBackupToAll:

    def setup_method(self):
        fake_config = MagicMock()
        fake_config.getVolumesConfig = MagicMock(return_value=FAKE_VOLUMES_CONFIG)
        fake_config.getHostsConfig = MagicMock(return_value=FAKE_HOSTS_CONFIG)
        sys.modules.setdefault("utils", MagicMock())
        sys.modules["utils.config"] = fake_config

        import importlib
        import classes.volume
        importlib.reload(classes.volume)

        self.vol_patcher = patch("classes.volume.getVolumesConfig", return_value=FAKE_VOLUMES_CONFIG)
        self.hosts_patcher = patch("classes.volume.getHostsConfig", return_value=FAKE_HOSTS_CONFIG)
        self.vol_patcher.start()
        self.hosts_patcher.start()

        from classes.volume import Volume
        self.Volume = Volume

        # Inject a fake classes.host module so the lazy `from classes.host import Host`
        # inside backupToAll doesn't attempt to import fabric (not installed in CI tests).
        self._host_targets = {
            "avalon":  make_target_host("avalon",  "avalon.l42.eu"),
            "xwing":   make_target_host("xwing",   "xwing.l42.eu"),
            "salvare": make_target_host("salvare",  "salvare.l42.eu"),
        }
        def host_factory(name):
            return self._host_targets[name]
        fake_host_module = type(sys)("classes.host")
        fake_host_module.Host = host_factory
        sys.modules["classes.host"] = fake_host_module

    def teardown_method(self):
        self.vol_patcher.stop()
        self.hosts_patcher.stop()
        sys.modules.pop("utils.config", None)
        sys.modules.pop("classes.host", None)

    def _make_volume(self, volume_name, host_name="avalon", host_domain="avalon.l42.eu", labels=None):
        if labels is None:
            labels = "com.docker.compose.project=lucos_photos"
        raw = make_raw_json(volume_name, labels=labels)
        host = make_host(host_name, host_domain)
        host.connection = MagicMock()
        vol = self.Volume(host, raw)
        vol.archiveLocally = MagicMock(return_value=("/srv/backups/local/volume/test.tar.gz", "2026-03-20"))
        return vol

    def test_backup_skips_destination_in_skip_backup_on_hosts(self, capsys):
        """Destinations listed in skip_backup_on_hosts must not receive the backup."""
        vol = self._make_volume("lucos_photos_photos")
        vol.backupToAll()

        calls = vol.host.copyFileTo.call_args_list
        destination_domains = [c[0][1].domain for c in calls]
        assert "salvare.l42.eu" not in destination_domains

    def test_backup_logs_skip_decision(self, capsys):
        """A skip decision should produce a visible log line naming the host and volume."""
        vol = self._make_volume("lucos_photos_photos")
        vol.backupToAll()

        captured = capsys.readouterr()
        assert "salvare" in captured.out
        assert "skip_backup_on_hosts" in captured.out
        assert "lucos_photos_photos" in captured.out

    def test_backup_sends_to_non_skipped_destinations(self):
        """Destinations not in skip_backup_on_hosts should still receive the backup."""
        vol = self._make_volume("lucos_photos_photos")
        vol.backupToAll()

        calls = vol.host.copyFileTo.call_args_list
        destination_domains = [c[0][1].domain for c in calls]
        assert "xwing.l42.eu" in destination_domains

    def test_backup_skips_source_host(self):
        """The source host should never receive its own backup (domain match)."""
        vol = self._make_volume("lucos_notes_data", labels="com.docker.compose.project=lucos_notes")
        vol.backupToAll()

        calls = vol.host.copyFileTo.call_args_list
        destination_domains = [c[0][1].domain for c in calls]
        assert "avalon.l42.eu" not in destination_domains

    def test_backup_with_no_skip_sends_to_all_remote_hosts(self):
        """A volume with no skip_backup_on_hosts sends to all non-source hosts."""
        vol = self._make_volume("lucos_notes_data", labels="com.docker.compose.project=lucos_notes")
        vol.backupToAll()

        calls = vol.host.copyFileTo.call_args_list
        destination_domains = [c[0][1].domain for c in calls]
        assert "xwing.l42.eu" in destination_domains
        assert "salvare.l42.eu" in destination_domains
        assert len(calls) == 2

    def test_target_path_uses_target_backup_root(self):
        """target_path passed to copyFileTo should use the target host's backup_root."""
        # Give one target host a non-default backup_root
        self._host_targets["xwing"].backup_root = "/backups/"
        vol = self._make_volume("lucos_notes_data", labels="com.docker.compose.project=lucos_notes")
        vol.backupToAll()

        calls = vol.host.copyFileTo.call_args_list
        xwing_call = next(c for c in calls if c[0][1].domain == "xwing.l42.eu")
        target_path = xwing_call[0][2]
        assert target_path.startswith("/backups/")

    def test_failure_on_one_host_does_not_abort_remaining_hosts(self):
        """If copyFileTo raises for one host, the remaining hosts are still attempted."""
        vol = self._make_volume("lucos_notes_data", labels="com.docker.compose.project=lucos_notes")

        def fail_on_xwing(source, target_host, target_path):
            if "xwing" in target_host.domain:
                raise Exception("disk full")

        vol.host.copyFileTo.side_effect = fail_on_xwing

        with pytest.raises(Exception):
            vol.backupToAll()

        calls = vol.host.copyFileTo.call_args_list
        destination_domains = [c[0][1].domain for c in calls]
        assert "xwing.l42.eu" in destination_domains
        assert "salvare.l42.eu" in destination_domains

    def test_failure_on_one_host_raises_summary_exception(self):
        """A failure on any host causes a summary exception after all hosts are tried."""
        vol = self._make_volume("lucos_notes_data", labels="com.docker.compose.project=lucos_notes")
        vol.host.copyFileTo.side_effect = Exception("scp failed")

        with pytest.raises(Exception) as exc_info:
            vol.backupToAll()

        assert "backupToAll failed" in str(exc_info.value)
        assert "2" in str(exc_info.value)

    def test_successful_hosts_still_receive_backup_when_one_fails(self, capsys):
        """Successful hosts are copied to even when another host's copyFileTo raises."""
        vol = self._make_volume("lucos_notes_data", labels="com.docker.compose.project=lucos_notes")

        hosts_attempted = []

        def selective_failure(source, target_host, target_path):
            hosts_attempted.append(target_host.domain)
            if "salvare" in target_host.domain:
                raise Exception("disk full")

        vol.host.copyFileTo.side_effect = selective_failure

        with pytest.raises(Exception):
            vol.backupToAll()

        assert "xwing.l42.eu" in hosts_attempted
        assert "salvare.l42.eu" in hosts_attempted

    def test_failure_logs_error_message(self, capsys):
        """A copyFileTo failure should produce a visible log line with the volume and hostname."""
        vol = self._make_volume("lucos_notes_data", labels="com.docker.compose.project=lucos_notes")
        vol.host.copyFileTo.side_effect = Exception("disk full")

        with pytest.raises(Exception):
            vol.backupToAll()

        captured = capsys.readouterr()
        assert "Failed to copy" in captured.out
        assert "lucos_notes_data" in captured.out
