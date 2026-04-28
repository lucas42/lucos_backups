"""
Unit tests for the Repository class.

Tests run from src/ with GITHUB_KEY set in the environment (or patched).
"""
import os
import sys
import pytest
from unittest.mock import MagicMock


FAKE_RAWINFO = {
    'name': 'lucos_photos',
    'size': 12345,
    'html_url': 'https://github.com/lucas42/lucos_photos',
    'archived': False,
    'fork': False,
    'url': 'https://api.github.com/repos/lucas42/lucos_photos',
}


def make_host(name, backup_root, is_storage_only=False):
    """Build a mock Host with name, backup_root, is_storage_only flag, and a recording connection.

    `is_storage_only` defaults to False so existing tests that pre-date the Bug D fix
    keep their pre-existing behaviour. The Bug D regression test below exercises the
    True path explicitly.
    """
    h = MagicMock()
    h.name = name
    h.backup_root = backup_root
    h.is_storage_only = is_storage_only
    h.connection = MagicMock()
    return h


class TestRepositoryStr:

    def setup_method(self):
        # Ensure GITHUB_KEY is set before importing repository (module-level guard)
        os.environ.setdefault("GITHUB_KEY", "test_key_for_unit_tests")

        # Stub classes.host (requires fabric, not in CI test deps)
        fake_host_module = type(sys)("classes.host")
        fake_host_module.Host = type("Host", (), {})
        sys.modules["classes.host"] = fake_host_module

        # Stub requests (not installed in CI test env — only pyyaml and pytest are)
        fake_requests = type(sys)("requests")
        fake_requests.get = lambda *a, **kw: None
        sys.modules["requests"] = fake_requests

        import importlib
        import classes.repository
        importlib.reload(classes.repository)
        from classes.repository import Repository
        self.Repository = Repository

    def teardown_method(self):
        sys.modules.pop("classes.host", None)
        sys.modules.pop("requests", None)
        sys.modules.pop("classes.repository", None)

    def test_str_returns_correct_format(self):
        """__str__ should return '<Repository name>' without referencing self.host."""
        repo = self.Repository(FAKE_RAWINFO)
        assert str(repo) == "<Repository lucos_photos>"

    def test_str_does_not_raise_attribute_error(self):
        """__str__ must not raise AttributeError (regression: copy-paste from OneOffFile referenced self.host)."""
        repo = self.Repository(FAKE_RAWINFO)
        # This should not raise AttributeError: 'Repository' object has no attribute 'host'
        result = str(repo)
        assert "Repository" in result
        assert "lucos_photos" in result


class TestRepositoryBackup:
    """Repository.backup must use each host's per-host `backup_root`,
    not a hardcoded `/srv/backups/`. Regression for the 2026-04-28 failure
    where every repo backup tried to mkdir `/srv/backups/external/...`
    on aurora (where that path is not writable)."""

    def setup_method(self):
        os.environ.setdefault("GITHUB_KEY", "test_key_for_unit_tests")

        # Stub classes.host (requires fabric, not in CI test deps).
        # We populate Host.getAll inside individual tests.
        fake_host_module = type(sys)("classes.host")
        fake_host_module.Host = type("Host", (), {})
        sys.modules["classes.host"] = fake_host_module

        # Stub requests so getAuthenticatedDownloadUrl can be monkey-patched per-test.
        fake_requests = type(sys)("requests")
        fake_requests.get = lambda *a, **kw: None
        sys.modules["requests"] = fake_requests

        import importlib
        import classes.repository
        importlib.reload(classes.repository)
        self.repository_module = classes.repository
        from classes.repository import Repository
        self.Repository = Repository

    def teardown_method(self):
        sys.modules.pop("classes.host", None)
        sys.modules.pop("requests", None)
        sys.modules.pop("classes.repository", None)

    def _make_repo_with_hosts(self, hosts):
        """Build a Repository whose backup() will iterate the given hosts.
        Stubs out the GitHub authenticated-URL fetch."""
        repo = self.Repository(FAKE_RAWINFO)
        repo.getAuthenticatedDownloadUrl = lambda: "https://example.invalid/tarball"
        # Replace Host.getAll on the stub module to return our test hosts.
        sys.modules["classes.host"].Host.getAll = staticmethod(lambda: hosts)
        return repo

    def test_backup_uses_each_host_backup_root_in_mkdir(self):
        """The mkdir command sent to each host must use that host's own backup_root.
        Regression: previously hardcoded `/srv/backups/external/github/repository`
        was sent to every host, including aurora (which has `/share/backups/`)."""
        avalon = make_host("avalon", "/srv/backups/")
        aurora = make_host("aurora", "/share/backups/")
        repo = self._make_repo_with_hosts([avalon, aurora])

        repo.backup()

        # First call on each host's connection is the mkdir command.
        avalon_mkdir = avalon.connection.run.call_args_list[0][0][0]
        aurora_mkdir = aurora.connection.run.call_args_list[0][0][0]
        assert avalon_mkdir == "mkdir -p /srv/backups/external/github/repository"
        assert aurora_mkdir == "mkdir -p /share/backups/external/github/repository"

    def test_backup_uses_each_host_backup_root_in_archive_path(self):
        """The wget archive path must also use each host's backup_root."""
        avalon = make_host("avalon", "/srv/backups/")
        aurora = make_host("aurora", "/share/backups/")
        repo = self._make_repo_with_hosts([avalon, aurora])

        repo.backup()

        # Second call on each host's connection is the wget command.
        avalon_wget = avalon.connection.run.call_args_list[1][0][0]
        aurora_wget = aurora.connection.run.call_args_list[1][0][0]
        assert "/srv/backups/external/github/repository/lucos_photos." in avalon_wget
        assert "/share/backups/external/github/repository/lucos_photos." in aurora_wget

    def test_backup_no_hardcoded_root_dir_constant(self):
        """The module should not export a hardcoded ROOT_DIR constant any more.
        Per-host paths come from each host's own backup_root."""
        assert not hasattr(self.repository_module, "ROOT_DIR")

    def test_backup_skips_storage_only_hosts(self):
        """Storage-only hosts (e.g. aurora) must be skipped entirely by Repository.backup —
        no mkdir, no wget, no closeConnection.

        Regression for the 2026-04-28 Bug D failure: aurora's bundled wget cannot
        negotiate modern TLS to GitHub, so when wget ran on aurora the per-repo
        loop in Repository.backup raised, broke out of `for host in Host.getAll()`,
        and prevented avalon/salvare/xwing from receiving their copies. With aurora
        first in the alphabetical iteration order, every repo ended up backed up to
        zero hosts.

        This regression test guards both the skip behaviour and the knock-on
        guarantee that non-storage-only hosts continue to receive backups when
        a storage-only host is present in the iteration.

        Note: this uses `is_storage_only` as a proxy for "can't reach external HTTPS",
        a deliberate conflation flagged in the code comment and tracked in #228."""
        aurora = make_host("aurora", "/share/backups/", is_storage_only=True)
        avalon = make_host("avalon", "/srv/backups/", is_storage_only=False)
        # aurora intentionally first to exercise the order-of-iteration consequence
        # that motivated this fix (a failure on the first host would otherwise abort
        # the whole loop).
        repo = self._make_repo_with_hosts([aurora, avalon])

        repo.backup()

        # aurora must have received no commands at all.
        assert aurora.connection.run.call_count == 0, \
            "Repository.backup must not run any command on a storage-only host"
        assert aurora.closeConnection.call_count == 0, \
            "Repository.backup must not even open/close a connection on a storage-only host"

        # avalon must still have received its mkdir + wget pair.
        assert avalon.connection.run.call_count == 2, \
            "Repository.backup must still send mkdir + wget to non-storage-only hosts"
        assert "mkdir -p /srv/backups/external/github/repository" == avalon.connection.run.call_args_list[0][0][0]
        assert "wget" in avalon.connection.run.call_args_list[1][0][0]
        assert avalon.closeConnection.call_count == 1

    def test_backup_does_not_skip_non_storage_only_hosts(self):
        """Belt-and-braces: when no host is storage-only, every host receives mkdir + wget.
        Guards against an over-broad skip that would skip hosts it shouldn't."""
        avalon = make_host("avalon", "/srv/backups/", is_storage_only=False)
        salvare = make_host("salvare", "/srv/backups/", is_storage_only=False)
        repo = self._make_repo_with_hosts([avalon, salvare])

        repo.backup()

        for host in (avalon, salvare):
            assert host.connection.run.call_count == 2, \
                "Each non-storage-only host should receive mkdir + wget"
