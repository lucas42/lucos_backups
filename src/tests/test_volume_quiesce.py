"""
Unit tests for the quiesce-during-read path (#344):
- Volume reads `quiesce` from config (default off)
- a quiesced archive pauses writers only around an uncompressed local capture,
  then compresses unpaused, and always clears its staging copy
- there is no fallback to a live tar when the capture fails
- quiesce + incremental is refused at backup time

Tests run from src/ so that effort_labels.yaml is accessible at module load.
"""
import json
import shlex
import sys
import pytest
from unittest.mock import MagicMock, patch


FAKE_VOLUMES_CONFIG = {
	"lucos_aithne_credential_store": {
		"description": "Auth store",
		"recreate_effort": "considerable",
		"quiesce": True,
	},
	"lucos_notes_data": {
		"description": "Notes data",
		"recreate_effort": "small",
	},
	"lucos_bad_combo": {
		"description": "Misconfigured",
		"recreate_effort": "small",
		"backup_strategy": "incremental",
		"quiesce": True,
	},
}

FAKE_HOSTS_CONFIG = {
	"avalon": {"domain": "avalon.l42.eu", "backup_root": "/srv/backups/"},
}


def make_raw_json(name):
	return json.dumps({
		"Name": name,
		"Mountpoint": "/var/lib/docker/volumes/{}/_data".format(name),
		"Labels": "com.docker.compose.project=lucos_aithne",
	})


def run_result(stdout=""):
	result = MagicMock()
	result.stdout = stdout
	return result


class TestQuiesce:

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

	def _make_volume(self, name="lucos_aithne_credential_store", writers="lucos_aithne\n"):
		host = MagicMock()
		host.name = "avalon"
		host.domain = "avalon.l42.eu"
		host.backup_root = "/srv/backups/"
		host.connection = MagicMock()

		def run(command, **kwargs):
			if command.startswith("docker ps --filter volume="):
				return run_result(writers)
			return run_result("Paused for 12ms: lucos_aithne")
		host.connection.run.side_effect = run
		return self.Volume(host, make_raw_json(name))

	def _commands(self, vol):
		return [c[0][0] for c in vol.host.connection.run.call_args_list]

	def test_quiesce_read_from_config(self):
		vol = self._make_volume()
		assert vol.quiesce is True
		assert vol.data["quiesce"] is True

	def test_quiesce_defaults_off(self):
		vol = self._make_volume("lucos_notes_data")
		assert vol.quiesce is False
		assert vol.data["quiesce"] is False

	def test_unknown_volume_is_not_quiesced(self):
		vol = self._make_volume("lucos_not_in_config")
		assert vol.quiesce is False

	def test_unquiesced_volume_keeps_the_plain_live_tar(self):
		vol = self._make_volume("lucos_notes_data")
		vol.archiveLocally()
		commands = self._commands(vol)
		assert not any("docker ps --filter" in c for c in commands)
		assert any("tar -C /raw-data -czf" in c for c in commands)

	def test_quiesced_archive_passes_discovered_writers_to_the_capture_script(self):
		vol = self._make_volume(writers="lucos_aithne\nlucos_aithne_sidecar\n")
		(archive_path, date) = vol.archiveLocally()

		capture = next(c for c in self._commands(vol) if c.startswith("sh -c "))
		argv = shlex.split(capture)
		# sh -c <script> <$0> <timeout> <watchdog> <volume> <mount> <staging> <writers...>
		assert "docker pause" in argv[2]
		assert argv[6:] == [
			"lucos_aithne_credential_store",
			"/srv/backups/local",
			"/srv/backups/local/.staging/lucos_aithne_credential_store.tar",
			"lucos_aithne",
			"lucos_aithne_sidecar",
		]
		assert archive_path == "/srv/backups/local/volume/lucos_aithne_credential_store.{}.tar.gz".format(date)

	def test_quiesced_archive_never_runs_a_live_compressed_tar(self):
		vol = self._make_volume()
		vol.archiveLocally()
		assert not any("-czf" in c for c in self._commands(vol))

	def test_compression_happens_after_capture_and_renames_into_place(self):
		vol = self._make_volume()
		(archive_path, _) = vol.archiveLocally()
		commands = self._commands(vol)
		capture_index = next(i for i, c in enumerate(commands) if c.startswith("sh -c "))
		gzip_index = next(i for i, c in enumerate(commands) if "gzip -c" in c)
		assert gzip_index > capture_index
		assert 'mv "$0.gz" "$1"' in commands[gzip_index]
		assert archive_path in commands[gzip_index]

	def test_staging_copy_is_removed_after_success(self):
		vol = self._make_volume()
		vol.archiveLocally()
		assert "rm -f /srv/backups/local/.staging/lucos_aithne_credential_store.tar" in self._commands(vol)[-1]

	def test_capture_failure_raises_cleans_up_and_does_not_compress(self):
		vol = self._make_volume()
		original = vol.host.connection.run.side_effect

		def run(command, **kwargs):
			if command.startswith("sh -c "):
				raise RuntimeError("capture exited 124")
			return original(command, **kwargs)
		vol.host.connection.run.side_effect = run

		with pytest.raises(RuntimeError, match="capture exited 124"):
			vol.archiveLocally()
		commands = self._commands(vol)
		assert not any("gzip" in c for c in commands)
		assert not any("-czf" in c for c in commands)
		assert "rm -f" in commands[-1]

	def test_cleanup_failure_does_not_mask_the_capture_error(self):
		vol = self._make_volume()
		original = vol.host.connection.run.side_effect

		def run(command, **kwargs):
			if command.startswith("sh -c "):
				raise RuntimeError("capture exited 70")
			if "rm -f" in command:
				raise RuntimeError("host went away")
			return original(command, **kwargs)
		vol.host.connection.run.side_effect = run

		with pytest.raises(RuntimeError, match="capture exited 70"):
			vol.archiveLocally()

	def test_capture_is_bounded_below_the_watchdog(self):
		import classes.volume as volume_module
		vol = self._make_volume()
		vol.archiveLocally()
		capture_call = next(c for c in vol.host.connection.run.call_args_list if c[0][0].startswith("sh -c "))
		assert capture_call[1]["timeout"] < volume_module.QUIESCE_WATCHDOG_SECONDS
		assert volume_module.QUIESCE_CAPTURE_TIMEOUT < volume_module.QUIESCE_WATCHDOG_SECONDS

	def test_no_writers_still_uses_the_staged_capture(self):
		vol = self._make_volume(writers="")
		vol.archiveLocally()
		capture = next(c for c in self._commands(vol) if c.startswith("sh -c "))
		assert shlex.split(capture)[-1].endswith(".staging/lucos_aithne_credential_store.tar")

	def test_quiesce_with_incremental_is_refused(self):
		vol = self._make_volume("lucos_bad_combo")
		vol.backupIncremental = MagicMock()
		with pytest.raises(Exception, match="quiesce"):
			vol.backup()
		vol.backupIncremental.assert_not_called()
