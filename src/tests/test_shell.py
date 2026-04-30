"""
Unit tests for GnuShell, BusyBoxShell, and Host is_storage_only short-circuit.

Tests run from src/ so effort_labels.yaml is accessible if needed.
No real SSH connections are made — connection and SFTP are fully mocked.
"""
import sys
import stat
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_connection():
    conn = MagicMock()
    conn.run = MagicMock()
    return conn


def make_sftp_attr(filename, size, mtime, mode=None):
    """Build a mock SFTPAttributes object."""
    attr = MagicMock()
    attr.filename = filename
    attr.st_size = size
    attr.st_mtime = mtime
    attr.st_mode = mode if mode is not None else stat.S_IFREG | 0o644
    return attr


def make_dir_attr(filename, mtime=1700000000):
    attr = MagicMock()
    attr.filename = filename
    attr.st_mtime = mtime
    attr.st_mode = stat.S_IFDIR | 0o755
    return attr


# ---------------------------------------------------------------------------
# GnuShell
# ---------------------------------------------------------------------------

class TestGnuShellDiskSpace:

    def setup_method(self):
        from classes.shell import GnuShell
        self.conn = make_connection()
        self.shell = GnuShell(self.conn, "/srv/backups/")

    def _run_result(self, text):
        r = MagicMock()
        r.stdout = text
        return r

    def test_disk_space_returns_correct_structure(self):
        """disk_space() returns free_bytes, free_readable, used_percentage."""
        self.conn.run.side_effect = [
            self._run_result("512000\n"),   # df -P free bytes (1K blocks)
            self._run_result("500M\n"),     # df -Ph readable
            self._run_result("42\n"),       # df -P used %
        ]
        result = self.shell.disk_space()
        assert result == {
            'free_bytes': 512000,
            'free_readable': "500M",
            'used_percentage': 42,
        }

    def test_disk_space_uses_backup_root(self):
        """disk_space() includes the backup_root path in the df commands."""
        self.conn.run.side_effect = [
            self._run_result("1024\n"),
            self._run_result("1M\n"),
            self._run_result("10\n"),
        ]
        self.shell.disk_space()
        for call_args in self.conn.run.call_args_list:
            assert "/srv/backups/" in call_args[0][0]


class TestGnuShellListOneOffFiles:

    def setup_method(self):
        from classes.shell import GnuShell
        self.conn = make_connection()
        self.shell = GnuShell(self.conn, "/srv/backups/")

    def _run_result(self, text):
        r = MagicMock()
        r.stdout = text
        return r

    def test_parses_ls_output(self):
        """list_one_off_files parses ls long format and returns normalised dicts.
        --time-style=long-iso produces 'YYYY-MM-DD HH:MM' — cols[5]=date, cols[6]=time, cols[7]=filename."""
        ls_output = (
            "total 8\n"
            "-rw-r--r-- 1 user group 1.2M 2026-01-15 10:30 archive.tar.gz\n"
        )
        self.conn.run.return_value = self._run_result(ls_output)
        result = self.shell.list_one_off_files("/srv/backups/local/one-off/")
        assert len(result) == 1
        assert result[0]["path"] == "/srv/backups/local/one-off/archive.tar.gz"
        assert result[0]["modification_date"] == "2026-01-15"
        assert result[0]["size"] == "1.2M"

    def test_empty_directory_returns_empty_list(self):
        """An empty directory (header only) returns an empty list."""
        self.conn.run.return_value = self._run_result("total 0\n")
        result = self.shell.list_one_off_files("/srv/backups/local/one-off/")
        assert result == []


class TestGnuShellFindBackupFiles:

    def setup_method(self):
        from classes.shell import GnuShell
        self.conn = make_connection()
        self.shell = GnuShell(self.conn, "/srv/backups/")

    def _run_result(self, text):
        r = MagicMock()
        r.stdout = text
        return r

    def test_returns_raw_lines(self):
        """find_backup_files returns the tab-separated lines from find output."""
        find_output = (
            "2026-01-01\t1024\t/srv/backups/host/xwing/volume/lucos_db.2026-01-01.tar.gz\n"
            "2026-01-02\t2048\t/srv/backups/host/xwing/volume/lucos_db.2026-01-02.tar.gz\n"
        )
        self.conn.run.return_value = self._run_result(find_output)
        result = self.shell.find_backup_files()
        assert len(result) == 2
        assert result[0].startswith("2026-01-01\t1024\t")

    def test_uses_backup_root_in_find_command(self):
        """find_backup_files passes backup_root to the find command."""
        self.conn.run.return_value = self._run_result("")
        self.shell.find_backup_files()
        cmd = self.conn.run.call_args[0][0]
        assert "/srv/backups/" in cmd


# ---------------------------------------------------------------------------
# BusyBoxShell
# ---------------------------------------------------------------------------

class TestBusyBoxShellDiskSpace:

    def setup_method(self):
        from classes.shell import BusyBoxShell
        self.conn = make_connection()
        self.shell = BusyBoxShell(self.conn, "/backups/")

    def _run_result(self, text):
        r = MagicMock()
        r.stdout = text
        return r

    def test_disk_space_parses_plain_df(self):
        """disk_space() parses plain BusyBox df output by column position."""
        df_output = (
            "Filesystem           1K-blocks      Used Available Use% Mounted on\n"
            "/dev/sda1              2097152    524288   1572864  25% /backups\n"
        )
        self.conn.run.return_value = self._run_result(df_output)
        result = self.shell.disk_space()
        assert result['free_bytes'] == 1572864 * 1024
        assert result['used_percentage'] == 25
        assert 'free_readable' in result

    def test_disk_space_uses_backup_root(self):
        """disk_space() passes the backup_root to df."""
        df_output = (
            "Filesystem 1K-blocks Used Available Use% Mounted\n"
            "/dev/sda1  1048576   0     1048576   0%   /backups\n"
        )
        self.conn.run.return_value = self._run_result(df_output)
        self.shell.disk_space()
        cmd = self.conn.run.call_args[0][0]
        assert "/backups/" in cmd


class TestBusyBoxShellListOneOffFiles:

    def setup_method(self):
        from classes.shell import BusyBoxShell
        self.conn = make_connection()
        self.sftp = MagicMock()
        self.conn.sftp.return_value = self.sftp
        self.shell = BusyBoxShell(self.conn, "/backups/")

    def test_returns_normalised_dicts(self):
        """list_one_off_files returns {path, modification_date, size} for each file."""
        mtime = datetime(2026, 3, 10).timestamp()
        self.sftp.listdir_attr.return_value = [
            make_sftp_attr("archive.tar.gz", size=1024*1024, mtime=mtime),
        ]
        result = self.shell.list_one_off_files("/backups/local/one-off/")
        assert len(result) == 1
        assert result[0]["path"] == "/backups/local/one-off/archive.tar.gz"
        assert result[0]["modification_date"] == "2026-03-10"
        assert result[0]["size"] == "1.0M"

    def test_hidden_files_are_excluded(self):
        """Files starting with '.' are excluded."""
        mtime = datetime(2026, 3, 10).timestamp()
        self.sftp.listdir_attr.return_value = [
            make_sftp_attr(".hidden", size=512, mtime=mtime),
            make_sftp_attr("visible.tar.gz", size=1024, mtime=mtime),
        ]
        result = self.shell.list_one_off_files("/backups/local/one-off/")
        assert len(result) == 1
        assert result[0]["path"].endswith("visible.tar.gz")

    def test_missing_directory_returns_empty_list(self):
        """FileNotFoundError from sftp returns an empty list."""
        self.sftp.listdir_attr.side_effect = FileNotFoundError
        result = self.shell.list_one_off_files("/backups/local/one-off/")
        assert result == []


class TestBusyBoxShellFindBackupFiles:

    def setup_method(self):
        from classes.shell import BusyBoxShell
        self.conn = make_connection()
        self.sftp = MagicMock()
        self.conn.sftp.return_value = self.sftp
        self.shell = BusyBoxShell(self.conn, "/backups/")

    def test_walks_tree_and_returns_tab_separated_strings(self):
        """find_backup_files returns 'YYYY-MM-DD\\tsize\\tpath' for deep files."""
        mtime = datetime(2026, 2, 14).timestamp()
        # Structure: /backups/host/xwing/volume/db.2026-02-14.tar.gz
        self.sftp.listdir_attr.side_effect = [
            # /backups/
            [make_dir_attr("host")],
            # /backups/host/
            [make_dir_attr("xwing")],
            # /backups/host/xwing/
            [make_dir_attr("volume")],
            # /backups/host/xwing/volume/
            [make_sftp_attr("db.2026-02-14.tar.gz", size=4096, mtime=mtime)],
        ]
        result = self.shell.find_backup_files()
        assert len(result) == 1
        assert "2026-02-14" in result[0]
        assert "4096" in result[0]
        assert "/backups/host/xwing/volume/db.2026-02-14.tar.gz" in result[0]

    def test_excludes_files_at_depth_one(self):
        """Files directly under backup_root (depth 1) are excluded (no nesting)."""
        mtime = datetime(2026, 2, 14).timestamp()
        # /backups/shallow.txt — only 1 path segment below root, should be excluded
        self.sftp.listdir_attr.side_effect = [
            [make_sftp_attr("shallow.txt", size=100, mtime=mtime)],
        ]
        result = self.shell.find_backup_files()
        assert result == []

    def test_hidden_files_excluded(self):
        """Hidden directories and files (starting with '.') are not traversed."""
        mtime = datetime(2026, 2, 14).timestamp()
        self.sftp.listdir_attr.side_effect = [
            [make_dir_attr(".hidden"), make_dir_attr("host")],
            # host/
            [make_dir_attr("xwing")],
            # host/xwing/
            [make_sftp_attr("file.tar.gz", size=1024, mtime=mtime)],
        ]
        result = self.shell.find_backup_files()
        # .hidden/ is not traversed, file.tar.gz at depth 2 is included
        assert len(result) == 1
        assert ".hidden" not in result[0]


# ---------------------------------------------------------------------------
# Host is_storage_only short-circuit
# ---------------------------------------------------------------------------

class TestHostOutboundSSH:
	"""Tests for _outbound_ssh_args and runOnRemote — specifically the ProxyJump logic.
	Regression guard for #160 (gateway flag bypassed by raw ssh subprocess paths)."""

	FAKE_HOSTS_CONFIG = {
		"avalon": {"domain": "avalon.s.l42.eu"},
		"aurora": {
			"domain": "aurora.local",
			"ssh_gateway": "xwing",
			"is_storage_only": True,
			"shell_flavour": "busybox",
			"backup_root": "/backups/",
		},
		"xwing": {"domain": "xwing.s.l42.eu"},
	}

	def setup_method(self):
		sys.modules.setdefault("utils", MagicMock())
		sys.modules["utils.config"] = MagicMock()

		# Stub fabric so Connection() returns a fresh MagicMock each time
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
		self.avalon = Host("avalon")    # no gateway
		self.aurora = Host("aurora")    # ssh_gateway: xwing → ssh_gateway_domain: xwing.s.l42.eu

	def teardown_method(self):
		self.host_patcher.stop()
		sys.modules.pop("utils.config", None)
		sys.modules.pop("utils", None)
		sys.modules.pop("fabric", None)
		sys.modules.pop("invoke", None)
		sys.modules.pop("classes.host", None)

	def test_outbound_ssh_args_no_gateway(self):
		"""_outbound_ssh_args returns only StrictHostKeyChecking=no when target has no gateway."""
		args = self.avalon._outbound_ssh_args(self.avalon)
		assert '-o' in args
		assert 'StrictHostKeyChecking=no' in args
		assert not any('ProxyJump' in a for a in args)

	def test_outbound_ssh_args_with_gateway(self):
		"""_outbound_ssh_args includes ProxyJump=<gateway_domain> when target has ssh_gateway."""
		args = self.avalon._outbound_ssh_args(self.aurora)
		proxyjump = next((a for a in args if 'ProxyJump' in a), None)
		assert proxyjump is not None, "ProxyJump flag must be present when target has ssh_gateway"
		assert 'xwing.s.l42.eu' in proxyjump

	def test_run_on_remote_with_gateway_uses_proxyjump(self):
		"""runOnRemote passes ProxyJump to the ssh command when the target has an ssh_gateway.
		This is the regression guard for #160 — previously the gateway was added to the Fabric
		Connection but the raw ssh subprocess call in runOnRemote bypassed it entirely."""
		self.avalon.runOnRemote(self.aurora, 'ls /backups')
		cmd = self.avalon.connection.run.call_args[0][0]
		assert 'ProxyJump' in cmd, "ssh command must contain ProxyJump flag"
		assert 'xwing.s.l42.eu' in cmd, "ProxyJump must point to the gateway domain"
		assert 'aurora.local' in cmd, "ssh command must target aurora's domain"

	def test_outbound_ssh_args_skips_proxyjump_when_source_is_gateway(self):
		"""When the source host IS the target's ssh_gateway, ProxyJump must be omitted.
		Regression for the 2026-04-28 incident: xwing→aurora cross-host copies failed
		with SSH error 255 because xwing was being told to ProxyJump through xwing
		to reach aurora (recursive). xwing should connect directly to aurora.local
		since it is the gateway by definition."""
		from classes.host import Host
		xwing = Host("xwing")
		args = xwing._outbound_ssh_args(self.aurora)
		# StrictHostKeyChecking should still be present.
		assert 'StrictHostKeyChecking=no' in args
		# ProxyJump must NOT be present.
		assert not any('ProxyJump' in a for a in args), \
			"ProxyJump must be omitted when source IS the target's ssh_gateway"

	def test_run_on_remote_no_proxyjump_when_source_is_gateway(self):
		"""runOnRemote on the gateway host must not include ProxyJump in the ssh command."""
		from classes.host import Host
		xwing = Host("xwing")
		xwing.runOnRemote(self.aurora, 'ls /backups')
		cmd = xwing.connection.run.call_args[0][0]
		assert 'ProxyJump' not in cmd, \
			"ssh command must NOT contain ProxyJump when source IS the gateway"
		assert 'aurora.local' in cmd, "ssh command must still target aurora's domain"


class TestHostStorageOnly:
    """Verify that a storage-only host skips volume and one-off file iteration."""

    def setup_method(self):
        FAKE_HOSTS_CONFIG = {
            "aurora": {
                "domain": "aurora.local",
                "is_storage_only": True,
                "backup_root": "/backups/",
                "shell_flavour": "busybox",
            },
        }
        fake_config = MagicMock()
        fake_config.getHostsConfig = MagicMock(return_value=FAKE_HOSTS_CONFIG)
        sys.modules.setdefault("utils", MagicMock())
        sys.modules["utils.config"] = fake_config

        # Stub fabric so Host.__init__ doesn't attempt real connections
        fake_fabric = MagicMock()
        fake_fabric.Connection = MagicMock(return_value=MagicMock())
        sys.modules["fabric"] = fake_fabric

        # Stub invoke
        sys.modules.setdefault("invoke", MagicMock())

        import importlib
        import classes.host
        importlib.reload(classes.host)

        self.host_patcher = patch("classes.host.getHostsConfig", return_value=FAKE_HOSTS_CONFIG)
        self.host_patcher.start()

        from classes.host import Host
        self.host = Host("aurora")

    def teardown_method(self):
        self.host_patcher.stop()
        sys.modules.pop("utils.config", None)
        sys.modules.pop("utils", None)
        sys.modules.pop("fabric", None)
        sys.modules.pop("classes.host", None)

    def test_get_volumes_returns_empty_for_storage_only(self):
        """getVolumes() returns [] immediately for a storage-only host."""
        result = self.host.getVolumes()
        assert result == []
        # Must not call docker volume ls
        self.host.connection.run.assert_not_called()

    def test_get_one_off_files_returns_empty_for_storage_only(self):
        """getOneOffFiles() returns [] immediately for a storage-only host."""
        result = self.host.getOneOffFiles()
        assert result == []
        self.host.connection.run.assert_not_called()


# ---------------------------------------------------------------------------
# Regression test: configy returns null for absent optional fields
# ---------------------------------------------------------------------------

class TestHostNullOptionalFields:
	"""Regression test for #221.

	configy.l42.eu serialises absent optional fields as explicit null (JSON null),
	not by omitting the key. dict.get(key, default) only uses the default when the
	key is absent — it returns None when the key is present with a null value.
	Hosts where backup_root and shell_flavour are absent in hosts.yaml therefore
	received None instead of their defaults, causing 'df -P None' and crashing the
	backup cron for every existing source host after the aurora integration landed.
	"""

	FAKE_HOSTS_CONFIG = {
		# Simulates the configy HTTP API shape: null values explicitly present
		"avalon": {
			"domain": "avalon.s.l42.eu",
			"backup_root": None,      # explicit null — not absent
			"is_storage_only": False,
			"shell_flavour": None,    # explicit null — not absent
			"ssh_gateway": None,
		},
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
		self.host = Host("avalon")

	def teardown_method(self):
		self.host_patcher.stop()
		sys.modules.pop("utils.config", None)
		sys.modules.pop("utils", None)
		sys.modules.pop("fabric", None)
		sys.modules.pop("invoke", None)
		sys.modules.pop("classes.host", None)

	def test_backup_root_defaults_to_srv_backups_when_configy_returns_null(self):
		"""backup_root falls back to /srv/backups/ when configy returns null (not absent).
		Regression guard for #221: dict.get(key, default) returns None for explicit null."""
		assert self.host.backup_root == "/srv/backups/"

	def test_shell_is_gnushell_when_shell_flavour_is_null(self):
		"""shell_flavour falls back to gnu when configy returns null.
		Regression guard for #221: a None shell_flavour must not select BusyBoxShell."""
		from classes.shell import GnuShell
		assert isinstance(self.host.shell, GnuShell)


class TestHostCanReachExternalServices:
	"""Tests for the can_reach_external_services field introduced in #228.

	This flag separates "this host can wget/curl from public HTTPS endpoints"
	from is_storage_only ("this host has no docker volumes of its own").

	The configy API now always returns an explicit boolean (defaulting True
	when absent from YAML), so host.py reads the value directly — no
	None-coalescing needed here.
	"""

	def _make_host_with_config(self, config_value):
		"""Construct a Host with a specific can_reach_external_services config value."""
		hosts_config = {
			"avalon": {
				"domain": "avalon.s.l42.eu",
				"backup_root": None,
				"is_storage_only": False,
				"shell_flavour": None,
				"ssh_gateway": None,
				"can_reach_external_services": config_value,
			},
		}
		sys.modules.setdefault("utils", MagicMock())
		sys.modules["utils.config"] = MagicMock()
		fake_fabric = MagicMock()
		fake_fabric.Connection = MagicMock(side_effect=lambda **kw: MagicMock())
		sys.modules["fabric"] = fake_fabric
		sys.modules.setdefault("invoke", MagicMock())

		import importlib
		import classes.host
		importlib.reload(classes.host)

		with patch("classes.host.getHostsConfig", return_value=hosts_config):
			from classes.host import Host
			host = Host("avalon")

		sys.modules.pop("utils.config", None)
		sys.modules.pop("utils", None)
		sys.modules.pop("fabric", None)
		sys.modules.pop("invoke", None)
		sys.modules.pop("classes.host", None)
		return host

	def test_explicit_false_is_honoured(self):
		"""When configy sends can_reach_external_services=false it must be respected.
		This is aurora's case — old OpenSSL, can't reach GitHub codeload."""
		host = self._make_host_with_config(False)
		assert host.can_reach_external_services is False

	def test_explicit_true_is_honoured(self):
		"""When configy sends can_reach_external_services=true it passes through."""
		host = self._make_host_with_config(True)
		assert host.can_reach_external_services is True
