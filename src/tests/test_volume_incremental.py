"""
Unit tests for the incremental (rsync --link-dest) backup strategy (ADR-0002):

- Volume reads `backup_strategy` from config (default full-snapshot; incremental opts in)
- Volume.backup() routes to the right path based on strategy
- Host.rsyncVolumeSnapshot() builds the correct container-delivered rsync command
  (link-dest rotation, resumable/atomic transfer, agent-socket mount, ProxyJump user)
- Host._latest_snapshot_date() picks the right previous snapshot for --link-dest
- Host.getSnapshotBackups() turns snapshot directories into recursive Backups

No real SSH connections are made — connection/SFTP are fully mocked.
"""
import json
import sys
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Volume: backup_strategy reading + routing
# ---------------------------------------------------------------------------

FAKE_VOLUMES_CONFIG = {
    "lucos_photos_photos": {
        "description": "Photo storage",
        "recreate_effort": "huge",
        "backup_strategy": "incremental",
        "skip_backup_on_hosts": ["salvare", "xwing"],
    },
    "lucos_notes_data": {
        "description": "Notes data",
        "recreate_effort": "small",
        # no backup_strategy → defaults to full-snapshot
    },
}

FAKE_HOSTS_CONFIG = {
    "avalon":  {"domain": "avalon.l42.eu",   "backup_root": "/srv/backups/"},
    "aurora":  {"domain": "aurora.local",    "backup_root": "/share/backups/"},
    "salvare": {"domain": "salvare.l42.eu",  "backup_root": "/srv/backups/"},
    "xwing":   {"domain": "xwing.l42.eu",    "backup_root": "/srv/backups/"},
}


def make_raw_json(name, project="lucos_photos"):
    return json.dumps({
        "Name": name,
        "Mountpoint": "/var/lib/docker/volumes/{}/_data".format(name),
        "Labels": "com.docker.compose.project=" + project,
    })


class TestVolumeStrategyRouting:

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

    def _make_volume(self, name, project="lucos_photos"):
        host = MagicMock()
        host.name = "avalon"
        host.domain = "avalon.l42.eu"
        host.backup_root = "/srv/backups/"
        return self.Volume(host, make_raw_json(name, project))

    def test_incremental_strategy_read_from_config(self):
        vol = self._make_volume("lucos_photos_photos")
        assert vol.backup_strategy == "incremental"
        assert vol.data["backup_strategy"] == "incremental"

    def test_strategy_defaults_to_full_snapshot_when_absent(self):
        vol = self._make_volume("lucos_notes_data", project="lucos_notes")
        assert vol.backup_strategy == "full-snapshot"

    def test_unknown_volume_defaults_to_full_snapshot(self):
        vol = self._make_volume("some_unknown_volume", project="lucos_unknown")
        assert vol.backup_strategy == "full-snapshot"

    def test_backup_routes_incremental_to_backup_incremental(self):
        vol = self._make_volume("lucos_photos_photos")
        vol.backupIncremental = MagicMock()
        vol.backupToAll = MagicMock()
        result = vol.backup()
        assert result == 1
        vol.backupIncremental.assert_called_once()
        vol.backupToAll.assert_not_called()

    def test_backup_routes_full_snapshot_to_backup_to_all(self):
        vol = self._make_volume("lucos_notes_data", project="lucos_notes")
        vol.backupIncremental = MagicMock()
        vol.backupToAll = MagicMock()
        result = vol.backup()
        assert result == 1
        vol.backupToAll.assert_called_once()
        vol.backupIncremental.assert_not_called()

    def test_backup_incremental_skips_listed_hosts_and_source(self):
        """backupIncremental rsyncs to aurora only (salvare+xwing skipped, avalon is source)."""
        # Patch the Host factory used inside backupIncremental
        target_hosts = {}
        for name, cfg in FAKE_HOSTS_CONFIG.items():
            h = MagicMock()
            h.name = name
            h.domain = cfg["domain"]
            h.backup_root = cfg["backup_root"]
            target_hosts[name] = h
        fake_host_module = type(sys)("classes.host")
        fake_host_module.Host = lambda name: target_hosts[name]
        sys.modules["classes.host"] = fake_host_module
        try:
            vol = self._make_volume("lucos_photos_photos")
            vol.host.rsyncVolumeSnapshot = MagicMock()
            vol.backupIncremental()
            # Only aurora should have received an rsync (salvare/xwing skipped, avalon is source)
            assert vol.host.rsyncVolumeSnapshot.call_count == 1
            called_target = vol.host.rsyncVolumeSnapshot.call_args[0][1]
            assert called_target.domain == "aurora.local"
        finally:
            sys.modules.pop("classes.host", None)


# ---------------------------------------------------------------------------
# Host.rsyncVolumeSnapshot + _latest_snapshot_date + getSnapshotBackups
# ---------------------------------------------------------------------------

class TestRsyncVolumeSnapshot:

    FAKE_HOSTS_CONFIG = {
        "avalon": {"domain": "avalon.l42.eu", "backup_root": "/srv/backups/"},
        "aurora": {
            "domain": "aurora.local",
            "ssh_gateway": "xwing",
            "is_storage_only": True,
            "shell_flavour": "busybox",
            "backup_root": "/share/backups/",
        },
        "xwing": {"domain": "xwing.l42.eu", "backup_root": "/srv/backups/"},
    }

    def setup_method(self):
        sys.modules.setdefault("utils", MagicMock())
        sys.modules["utils.config"] = MagicMock()
        fake_fabric = MagicMock()
        fake_fabric.Connection = MagicMock(side_effect=lambda **kw: MagicMock())
        sys.modules["fabric"] = fake_fabric
        sys.modules.setdefault("invoke", MagicMock())

        import importlib
        import classes.host
        importlib.reload(classes.host)

        self.host_patcher = patch("classes.host.getHostsConfig", return_value=self.FAKE_HOSTS_CONFIG)
        self.host_patcher.start()

        from classes.host import Host
        self.avalon = Host("avalon")
        self.aurora = Host("aurora")

    def teardown_method(self):
        self.host_patcher.stop()
        sys.modules.pop("utils.config", None)
        sys.modules.pop("utils", None)
        sys.modules.pop("fabric", None)
        sys.modules.pop("invoke", None)
        sys.modules.pop("classes.host", None)

    def _run_factory(self, ls_output=""):
        """A connection.run side_effect that returns ls_output for the ls -1 call."""
        def run(*args, **kwargs):
            cmd = args[0]
            r = MagicMock()
            r.stdout = ls_output if "ls -1" in cmd else ""
            return r
        return run

    def _docker_command(self):
        for c in self.avalon.connection.run.call_args_list:
            if "docker run" in c[0][0]:
                return c[0][0]
        return None

    def test_rsync_runs_in_container_with_agent_socket(self):
        self.avalon.connection.run.side_effect = self._run_factory()
        self.avalon.rsyncVolumeSnapshot("lucos_photos_photos", self.aurora, "2026-06-10")
        cmd = self._docker_command()
        assert cmd is not None, "a docker run rsync command must be issued"
        assert "docker run --rm" in cmd
        assert "lucas42/lucos_backups:" in cmd  # container-delivered tooling
        assert "rsync" in cmd
        # Forwarded agent socket mounted in so the container can reach the target
        assert '"$SSH_AUTH_SOCK":/ssh-agent' in cmd
        assert "SSH_AUTH_SOCK=/ssh-agent" in cmd
        # Volume mounted read-only
        assert "lucos_photos_photos:/raw-data:ro" in cmd

    def test_rsync_is_resumable_and_targets_partial(self):
        self.avalon.connection.run.side_effect = self._run_factory()
        self.avalon.rsyncVolumeSnapshot("lucos_photos_photos", self.aurora, "2026-06-10")
        cmd = self._docker_command()
        assert "--partial" in cmd
        assert "--append-verify" in cmd
        # Transfer lands in <date>.partial, never the final dir directly
        assert "/2026-06-10.partial/" in cmd

    def test_rsync_uses_proxyjump_with_user_to_reach_gateway_target(self):
        self.avalon.connection.run.side_effect = self._run_factory()
        self.avalon.rsyncVolumeSnapshot("lucos_photos_photos", self.aurora, "2026-06-10")
        cmd = self._docker_command()
        # In-container ssh runs as root, so the ProxyJump host must be user-qualified
        assert "ProxyJump=lucos-backups@xwing.l42.eu" in cmd
        # rsync target is user-qualified too
        assert "lucos-backups@aurora.local:" in cmd

    def test_rsync_mounts_host_known_hosts_for_hostkey_verification(self):
        # Regression test for #327: the in-container (root) ssh has no known_hosts
        # of its own, and StrictHostKeyChecking=no does NOT propagate to the
        # ProxyJump hop — so without the host user's known_hosts mounted in, the
        # jump to the gateway fails host-key verification. Mount must be read-only.
        self.avalon.connection.run.side_effect = self._run_factory()
        self.avalon.rsyncVolumeSnapshot("lucos_photos_photos", self.aurora, "2026-06-10")
        cmd = self._docker_command()
        assert "/home/lucos-backups/.ssh/known_hosts:/root/.ssh/known_hosts:ro" in cmd

    def test_rsync_aborts_when_known_hosts_missing(self):
        # Regression guard for #327: a missing known_hosts on the source host would
        # make Docker create a *directory* at the bind-mount source (corrupting the
        # user's .ssh), so the rsync must abort BEFORE the docker run rather than
        # mount an unusable path. The check runs over SSH on the source host.
        import classes.host
        class FakeUnexpectedExit(Exception):
            pass
        classes.host.invoke.exceptions.UnexpectedExit = FakeUnexpectedExit

        def run(*args, **kwargs):
            cmd = args[0]
            if cmd.startswith("test -f"):  # known_hosts existence probe
                raise FakeUnexpectedExit("file not found")
            r = MagicMock()
            r.stdout = ""
            return r
        self.avalon.connection.run.side_effect = run

        with pytest.raises(RuntimeError, match="known_hosts not found"):
            self.avalon.rsyncVolumeSnapshot("lucos_photos_photos", self.aurora, "2026-06-10")
        # Must abort before issuing any docker run
        assert self._docker_command() is None

    def test_link_dest_used_when_previous_snapshot_exists(self):
        # ls returns prior dates + today's stale partial; today and partial must be ignored
        self.avalon.connection.run.side_effect = self._run_factory(
            "2026-06-08\n2026-06-09\n2026-06-10.partial\n"
        )
        self.avalon.rsyncVolumeSnapshot("lucos_photos_photos", self.aurora, "2026-06-10")
        cmd = self._docker_command()
        # Most recent prior date (2026-06-09) is the link-dest
        assert "--link-dest=/share/backups/host/avalon/volume-snapshots/lucos_photos_photos/2026-06-09/" in cmd

    def test_no_link_dest_on_first_snapshot(self):
        self.avalon.connection.run.side_effect = self._run_factory("")  # empty dir
        self.avalon.rsyncVolumeSnapshot("lucos_photos_photos", self.aurora, "2026-06-10")
        cmd = self._docker_command()
        assert "--link-dest" not in cmd

    def test_atomic_publish_rename_after_transfer(self):
        self.avalon.connection.run.side_effect = self._run_factory()
        self.avalon.rsyncVolumeSnapshot("lucos_photos_photos", self.aurora, "2026-06-10")
        # The publish step renames .partial -> final on the target
        publish = [c[0][0] for c in self.avalon.connection.run.call_args_list
                   if "mv " in c[0][0] and ".partial" in c[0][0]]
        assert publish, "an atomic mv of .partial -> final must be issued"
        assert "rm -rf" in publish[0]  # replace any previous same-day snapshot first
        assert "2026-06-10.partial" in publish[0]

    def test_latest_snapshot_date_ignores_today_and_non_dates(self):
        self.avalon.connection.run.side_effect = self._run_factory(
            "2026-06-08\n2026-06-09\n2026-06-10\n2026-06-10.partial\nnotadate\n"
        )
        result = self.avalon._latest_snapshot_date(
            self.aurora, "/share/backups/host/avalon/volume-snapshots/lucos_photos_photos/", "2026-06-10"
        )
        assert result == "2026-06-09"

    def test_latest_snapshot_date_none_when_dir_missing(self):
        # `invoke` is stubbed with a MagicMock in this harness, so give host.py a
        # real exception class to catch, and raise it from the ls call.
        import classes.host
        class FakeUnexpectedExit(Exception):
            pass
        classes.host.invoke.exceptions.UnexpectedExit = FakeUnexpectedExit
        self.avalon.connection.run.side_effect = FakeUnexpectedExit()
        result = self.avalon._latest_snapshot_date(self.aurora, "/nope/", "2026-06-10")
        assert result is None

    def test_get_snapshot_backups_builds_recursive_volume_snapshot(self):
        # aurora stores snapshots from avalon; find_snapshot_dirs returns dated dirs
        self.aurora.shell = MagicMock()
        self.aurora.shell.find_snapshot_dirs.return_value = [
            "/share/backups/host/avalon/volume-snapshots/lucos_photos_photos/2026-06-09",
            "/share/backups/host/avalon/volume-snapshots/lucos_photos_photos/2026-06-10",
            "/share/backups/host/avalon/volume-snapshots/lucos_photos_photos/2026-06-11.partial",
        ]
        backups = self.aurora.getSnapshotBackups()
        assert len(backups) == 1
        b = backups[0]
        assert b.type == "volume-snapshot"
        assert b.name == "lucos_photos_photos"
        assert b.source_hostname == "avalon"
        assert b.recursive is True
        # .partial is skipped (not a parseable date); two real snapshots remain
        assert len(b.instances) == 2
