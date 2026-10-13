"""Tests for Host.restoreMissingOneOffFiles and the create-backups heal hook."""
import sys
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(scope="module")
def host_module():
    sys.modules.pop("classes.host", None)
    fake = {"fabric": MagicMock(), "invoke": MagicMock(), "yaml": MagicMock(), "utils.config": MagicMock()}
    with patch.dict("sys.modules", fake):
        import classes.host as h
        yield h
    sys.modules.pop("classes.host", None)


def _host(host_module, name, local=None, copies=None, storage_only=False):
    """A Host whose shell/ssh are mocked. local: {name: size} in local/one-off/; copies: {name: size} held for the healed host."""
    h = host_module.Host.__new__(host_module.Host)
    h.name = name
    h.domain = name + ".test"
    h.is_storage_only = storage_only
    h.backup_root = "/srv/backups/"
    h.shell = MagicMock()
    h.shell.one_off_sizes.side_effect = lambda d: dict(local or {}) if "/local/" in d else dict(copies or {})
    h.copyTo = MagicMock()
    return h


def _heal_target(host_module, local=None, staged_size=None):
    target = _host(host_module, "avalon", local=local)
    target.shell.file_size.side_effect = lambda path: staged_size
    return target


def test_heals_via_staging_then_rename(host_module):
    target = _heal_target(host_module, staged_size=100)
    xwing = _host(host_module, "xwing", copies={"a.tar": 100})
    healed, failed = target.restoreMissingOneOffFiles([target, xwing])
    assert (healed, failed) == (["a.tar"], [])
    xwing.copyTo.assert_called_once_with(target, "/srv/backups/host/avalon/one-off/a.tar", "/srv/backups/local/one-off/.heal-a.tar")
    target.shell.move_file.assert_called_once_with("/srv/backups/local/one-off/.heal-a.tar", "/srv/backups/local/one-off/a.tar")


def test_finds_copy_only_on_storage_only_host(host_module):
    target = _heal_target(host_module, staged_size=7)
    aurora = _host(host_module, "aurora", copies={"b.tar": 7}, storage_only=True)
    healed, _ = target.restoreMissingOneOffFiles([target, aurora])
    assert healed == ["b.tar"]


def test_disagreeing_sizes_not_healed(host_module):
    target = _heal_target(host_module, staged_size=100)
    xwing = _host(host_module, "xwing", copies={"a.tar": 100})
    salvare = _host(host_module, "salvare", copies={"a.tar": 90})
    healed, failed = target.restoreMissingOneOffFiles([target, xwing, salvare])
    assert (healed, failed) == ([], ["a.tar"])
    xwing.copyTo.assert_not_called()
    salvare.copyTo.assert_not_called()
    target.shell.move_file.assert_not_called()


def test_staged_size_mismatch_removes_staging_and_does_not_rename(host_module):
    target = _heal_target(host_module, staged_size=50)
    xwing = _host(host_module, "xwing", copies={"a.tar": 100})
    healed, failed = target.restoreMissingOneOffFiles([target, xwing])
    assert (healed, failed) == ([], ["a.tar"])
    target.shell.remove_file.assert_called_with("/srv/backups/local/one-off/.heal-a.tar")
    target.shell.move_file.assert_not_called()


def test_falls_back_to_next_copy_when_one_fails(host_module):
    target = _heal_target(host_module, staged_size=100)
    xwing = _host(host_module, "xwing", copies={"a.tar": 100})
    xwing.copyTo.side_effect = Exception("ssh failed")
    salvare = _host(host_module, "salvare", copies={"a.tar": 100})
    healed, failed = target.restoreMissingOneOffFiles([target, xwing, salvare])
    assert (healed, failed) == (["a.tar"], [])


def test_nothing_missing_does_nothing(host_module):
    target = _heal_target(host_module, local={"a.tar": 100})
    xwing = _host(host_module, "xwing", copies={"a.tar": 100})
    assert target.restoreMissingOneOffFiles([target, xwing]) == ([], [])
    xwing.copyTo.assert_not_called()


def test_storage_only_host_is_not_a_heal_target(host_module):
    aurora = _host(host_module, "aurora", storage_only=True)
    assert aurora.restoreMissingOneOffFiles([aurora]) == ([], [])
