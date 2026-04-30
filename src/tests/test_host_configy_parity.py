"""
Dev/prod parity tests for configy API response shape.

Background (incident 2026-04-28):
  The local development YAML (config.yaml / hosts.yaml) omits absent optional
  fields.  The production configy HTTP API returns those fields explicitly as
  null.  dict.get(key, default) only uses the default when the key is absent —
  it returns None when the key is present with a null value.  This mismatch was
  invisible in local testing but fatal in production (#221).

What this file tests:
  - Load the fixture src/tests/fixtures/configy_hosts_api.yaml, which mirrors
    the production API response shape (explicit null for absent optional fields).
  - Parse it through the same logic as utils/config.py (list → dict keyed by id).
  - Assert that Host.__init__ handles every optional field correctly when the
    field is explicitly null, covering all four documented optional fields:
      * backup_root     → fallback to /srv/backups/
      * shell_flavour   → fallback to gnu (GnuShell)
      * is_storage_only → treated as False (not storage-only)
      * ssh_gateway     → treated as None (no gateway, direct connection)

Regenerating the fixture:
  If the configy schema changes, re-fetch from the production API:
    curl -s -H 'Accept: application/x-yaml' https://configy.l42.eu/hosts \\
      | python3 -c 'import sys,yaml; data=yaml.safe_load(sys.stdin);
          [d.update({k: None for k in ["backup_root","shell_flavour","is_storage_only","ssh_gateway"] if k not in d}) for d in data];
          print(yaml.dump(data))'
  Then update tests/fixtures/configy_hosts_api.yaml.

Tests run from src/ — no live network calls are made.
"""
import os
import sys
import yaml
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Load the fixture once — same parsing logic as utils/config.py fetchConfig()
# ---------------------------------------------------------------------------

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "configy_hosts_api.yaml")

def load_fixture_hosts():
    """Parse the fixture YAML and return a dict keyed by host id, as config.py does."""
    with open(FIXTURE_PATH) as f:
        host_list = yaml.safe_load(f)
    hosts_config = {}
    for host in host_list:
        hosts_config[host["id"]] = host
    return hosts_config


HOSTS_CONFIG = load_fixture_hosts()


# ---------------------------------------------------------------------------
# Shared setup/teardown for Host instantiation
# ---------------------------------------------------------------------------

class HostTestBase:
    """
    Provides setup_method / teardown_method that stub out the three external
    dependencies of classes/host.py (fabric, invoke, utils.config) so that
    Host objects can be constructed without a live SSH connection or configy.
    """

    def _setup_host_class(self, hosts_config):
        sys.modules.setdefault("utils", MagicMock())
        sys.modules["utils.config"] = MagicMock()

        fake_fabric = MagicMock()
        # Return a fresh MagicMock per Connection() call so hosts don't share state
        fake_fabric.Connection = MagicMock(side_effect=lambda **kw: MagicMock())
        sys.modules["fabric"] = fake_fabric
        sys.modules.setdefault("invoke", MagicMock())

        import importlib
        import classes.host
        importlib.reload(classes.host)

        self.host_patcher = patch("classes.host.getHostsConfig", return_value=hosts_config)
        self.host_patcher.start()

        from classes.host import Host
        self.Host = Host

    def teardown_method(self):
        self.host_patcher.stop()
        for mod in ("utils.config", "utils", "fabric", "invoke", "classes.host"):
            sys.modules.pop(mod, None)


# ---------------------------------------------------------------------------
# Tests: null optional fields use the correct defaults
# ---------------------------------------------------------------------------

class TestNullBackupRoot(HostTestBase):
    """backup_root: null in configy API → Host should use /srv/backups/"""

    def setup_method(self):
        self._setup_host_class(HOSTS_CONFIG)

    def test_null_backup_root_falls_back_to_default(self):
        """When configy sends backup_root=null (not absent), Host uses /srv/backups/.

        Regression guard for #221: dict.get('backup_root', '/srv/backups/') returns
        None when the key is present with a null value, not the default string."""
        host = self.Host("avalon")
        assert host.backup_root == "/srv/backups/", (
            "backup_root should be '/srv/backups/' when configy sends null, "
            "not None (which caused 'df -P None' in the 2026-04-28 incident)"
        )

    def test_explicit_backup_root_is_used_unchanged(self):
        """When configy sends a real backup_root value it is used as-is."""
        host = self.Host("aurora")
        assert host.backup_root == "/share/backups/"

    def test_null_backup_root_is_not_none(self):
        """backup_root must never be None — a None path crashes shell commands."""
        host = self.Host("avalon")
        assert host.backup_root is not None


class TestNullShellFlavour(HostTestBase):
    """shell_flavour: null in configy API → Host should use GnuShell"""

    def setup_method(self):
        self._setup_host_class(HOSTS_CONFIG)

    def test_null_shell_flavour_selects_gnu_shell(self):
        """When configy sends shell_flavour=null (not absent), Host instantiates GnuShell.

        Regression guard for #221: a None shell_flavour must select the gnu default,
        not fall through to BusyBoxShell."""
        from classes.shell import GnuShell
        host = self.Host("avalon")
        assert isinstance(host.shell, GnuShell), (
            "shell_flavour=null must produce GnuShell, not BusyBoxShell"
        )

    def test_busybox_shell_flavour_selects_busybox(self):
        """An explicit 'busybox' shell_flavour still selects BusyBoxShell."""
        from classes.shell import BusyBoxShell
        host = self.Host("aurora")
        assert isinstance(host.shell, BusyBoxShell)


class TestNullIsStorageOnly(HostTestBase):
    """is_storage_only: null in configy API → Host should not treat host as storage-only"""

    def setup_method(self):
        self._setup_host_class(HOSTS_CONFIG)

    def test_null_is_storage_only_treated_as_false(self):
        """When configy sends is_storage_only=null, the host is not storage-only.

        None is falsy in Python, but this test documents the expected behaviour
        explicitly so a future refactor that changes the attribute type
        (e.g. raising on None) would be caught here."""
        host = self.Host("avalon")
        assert host.is_storage_only is False, (
            "is_storage_only=null must be False (not None) — dict.get(key, False) "
            "returns None when the key is present with a null value, which is the "
            "same bug class as the 2026-04-28 incident"
        )

    def test_explicit_true_is_storage_only_is_respected(self):
        """An explicitly-set is_storage_only=true is passed through correctly."""
        host = self.Host("aurora")
        assert host.is_storage_only

    def test_null_is_storage_only_does_not_skip_volumes(self):
        """getVolumes() on a null-is_storage_only host should attempt to list volumes
        (i.e. not short-circuit as if it were storage-only)."""
        host = self.Host("avalon")
        # getVolumes calls connection.run — verify it is not vacuously bypassed
        # We don't care about the return value (run returns a MagicMock), just that
        # the call is attempted rather than skipped.
        try:
            host.getVolumes()
        except Exception:
            pass  # connection.run may raise; we only care it was called
        host.connection.run.assert_called()


class TestNullSshGateway(HostTestBase):
    """ssh_gateway: null in configy API → Host should make a direct connection"""

    def setup_method(self):
        self._setup_host_class(HOSTS_CONFIG)

    def test_null_ssh_gateway_means_direct_connection(self):
        """When configy sends ssh_gateway=null, Host.ssh_gateway is falsy."""
        host = self.Host("avalon")
        assert not host.ssh_gateway, (
            "ssh_gateway=null must not activate gateway logic"
        )

    def test_null_ssh_gateway_domain_is_none(self):
        """When ssh_gateway is null, ssh_gateway_domain should be None."""
        host = self.Host("avalon")
        assert host.ssh_gateway_domain is None

    def test_explicit_ssh_gateway_activates_proxy(self):
        """When ssh_gateway is set to a host name, the host sets ssh_gateway_domain."""
        host = self.Host("salvare")
        # salvare has ssh_gateway: xwing; xwing's domain is xwing.s.l42.eu
        assert host.ssh_gateway == "xwing"
        assert host.ssh_gateway_domain == "xwing.s.l42.eu"


# ---------------------------------------------------------------------------
# Test: fixture itself has the right shape (API parity guard)
# ---------------------------------------------------------------------------

class TestFixtureShape:
    """Verify that the fixture has the shape the production configy API returns.

    These tests guard against the fixture itself drifting away from the API
    shape it is meant to represent."""

    def test_fixture_is_a_list(self):
        """The fixture should be a YAML list (as the API endpoint returns),
        not a dict (as the local config.yaml stores it after parsing)."""
        with open(FIXTURE_PATH) as f:
            raw = yaml.safe_load(f)
        assert isinstance(raw, list), "Fixture must be a YAML list, matching the configy API"

    def test_each_host_has_id_field(self):
        """Each host in the fixture must have an 'id' field (used as the dict key)."""
        with open(FIXTURE_PATH) as f:
            raw = yaml.safe_load(f)
        for host in raw:
            assert "id" in host, f"Host {host} is missing 'id' field"

    def test_optional_fields_are_present_for_null_hosts(self):
        """Optional fields must be explicitly present with null values (not absent).

        This is the key difference from the local YAML: the API serialises
        absent optional fields as explicit null, not by omitting the key."""
        optional_fields = {"backup_root", "shell_flavour", "is_storage_only", "ssh_gateway"}
        with open(FIXTURE_PATH) as f:
            raw = yaml.safe_load(f)

        # Find a host where all optional fields are null
        null_hosts = [h for h in raw if all(h.get(f) is None for f in optional_fields)]
        assert null_hosts, (
            "Fixture must contain at least one host with ALL optional fields "
            "explicitly null (not absent) to represent the production API shape. "
            f"Optional fields checked: {optional_fields}"
        )

        # Confirm those fields are explicitly present (key exists), not just absent
        for host in null_hosts:
            for field in optional_fields:
                assert field in host, (
                    f"Field '{field}' must be present with a null value in host "
                    f"'{host.get('id')}', not absent. The API always sends the key."
                )

    def test_fixture_contains_host_with_set_optional_fields(self):
        """Fixture should also include a host with some optional fields set,
        to verify that non-null values are passed through correctly."""
        with open(FIXTURE_PATH) as f:
            raw = yaml.safe_load(f)
        hosts_with_backup_root = [h for h in raw if h.get("backup_root")]
        assert hosts_with_backup_root, (
            "Fixture must contain at least one host with a non-null backup_root "
            "to test that explicit values are used when present."
        )
