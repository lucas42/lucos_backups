"""
Unit tests for the Repository class.

Tests run from src/ with GITHUB_KEY set in the environment (or patched).
"""
import os
import sys
import pytest


FAKE_RAWINFO = {
    'name': 'lucos_photos',
    'size': 12345,
    'html_url': 'https://github.com/lucas42/lucos_photos',
    'archived': False,
    'fork': False,
    'url': 'https://api.github.com/repos/lucas42/lucos_photos',
}


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
