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


def make_host(name, backup_root):
    """Build a mock Host with name, backup_root, and a recording connection."""
    h = MagicMock()
    h.name = name
    h.backup_root = backup_root
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
