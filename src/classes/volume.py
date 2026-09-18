'''
Volume
A particular docker volume stored on given `Host`
'''
import yaml
import json
import os
import shlex
from datetime import datetime
from utils.config import getVolumesConfig, getHostsConfig

with open("effort_labels.yaml") as effort_labels_yaml:
	effort_labels = yaml.safe_load(effort_labels_yaml)

# Runs on the source host to pause a volume's writers around a fast local read.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "quiesce-capture.sh")) as quiesce_script_file:
	QUIESCE_CAPTURE_SCRIPT = quiesce_script_file.read()

# Upper bound on the paused read; exceeding it unpauses and fails the backup.
QUIESCE_CAPTURE_TIMEOUT = 60
# Independent backstop that unpauses even if the capture script is killed outright.
QUIESCE_WATCHDOG_SECONDS = 120

class Volume:
	def __init__(self, host, rawjson):
		self.host = host
		data = json.loads(rawjson)
		self.name = data["Name"]
		self.path = data["Mountpoint"]
		if self.name in getVolumesConfig():
			known = True
			description = getVolumesConfig()[self.name]["description"]
			effort_id = getVolumesConfig()[self.name]["recreate_effort"]
			skip_backup = getVolumesConfig()[self.name].get("skip_backup", False)
			skip_backup_on_hosts = getVolumesConfig()[self.name].get("skip_backup_on_hosts", [])
			# Absent / null / "full-snapshot" → the default daily full tar+scp.
			# "incremental" → rsync --link-dest hardlink-rotated snapshots (ADR-0002).
			backup_strategy = getVolumesConfig()[self.name].get("backup_strategy") or "full-snapshot"
			# Pause the volume's running writers during the local read (#344).
			quiesce = getVolumesConfig()[self.name].get("quiesce") or False
		else:
			known = False
			description = "Unknown Volume"
			effort_id = "unknown"
			skip_backup = False
			skip_backup_on_hosts = []
			backup_strategy = "full-snapshot"
			quiesce = False
		self.backup_strategy = backup_strategy
		self.quiesce = quiesce
		labels = {}
		if data["Labels"]:
			for label in data["Labels"].split(","):
				key, value = label.split("=", 1)
				labels[key] = value
		if 'com.docker.compose.project' not in labels:
			raise Exception("No Docker Compose project label on volume "+self.name)
		project = labels['com.docker.compose.project']

		if effort_id not in effort_labels:
			print("\033[93m** Warn ** Unknown recreate_effort '{}' for volume {} — falling back to 'unknown'\033[0m".format(effort_id, self.name), flush=True)
			effort_id = "unknown"
		self.effort = {
			'id': effort_id,
			'label': effort_labels[effort_id],
		}
		self.data = {
			'name': self.name,
			'description': description,
			'known': known,
			'effort': self.effort,
			'skip_backup': skip_backup,
			'skip_backup_on_hosts': skip_backup_on_hosts,
			'backup_strategy': backup_strategy,
			'quiesce': quiesce,
			'project': {
				'name': project,
				'link': "https://github.com/lucas42/"+project,
			},
			'source_host': self.host.name
		}

	def __str__(self):
		return "<Volume {} on {}>".format(self.name, self.host.name)

	# Creates a compressed tarball of the volume and saves it on the local drive
	# NB: will replace any existing tarball for a volume of the same name
	def archiveLocally(self):
		print("Creating local archive of "+str(self), flush=True)
		archiveDirectory = self.host.backup_root + "local/volume"
		date = datetime.today().strftime('%Y-%m-%d')
		archivePath = "{archive_directory}/{volume_name}.{date}.tar.gz".format(archive_directory=archiveDirectory, volume_name=self.name, date=date)
		self.host.connection.run("mkdir -p {}".format(archiveDirectory), timeout=3)
		if self.quiesce:
			self.archiveQuiesced(archivePath)
			return (archivePath, date)
		self.host.connection.run("docker run --rm --volume {volume_name}:/raw-data --mount src={archive_directory},target={archive_directory},type=bind alpine:latest tar -C /raw-data -czf {archive_path} .".format(
			volume_name=self.name,
			archive_directory=archiveDirectory,
			archive_path=archivePath,
		), timeout=600)
		return (archivePath, date)

	# The running containers that mount this volume, i.e. its potential writers.
	def findWriters(self):
		result = self.host.connection.run(
			"docker ps --filter volume={} --format '{{{{.Names}}}}'".format(shlex.quote(self.name)),
			hide=True, timeout=10,
		)
		return [name.strip() for name in result.stdout.splitlines() if name.strip()]

	# Like the plain archive, but the volume's writers are paused for the read, so
	# the archive is one point in time rather than a smear across live writes.
	# Only an uncompressed local capture happens while paused; gzip runs after.
	# Any failure raises: there is deliberately no fallback to a live tar.
	def archiveQuiesced(self, archivePath):
		localRoot = self.host.backup_root + "local"
		# Dot-prefixed so neither find_backup_files walker lists it as a backup.
		stagingPath = "{}/.staging/{}.tar".format(localRoot, self.name)
		self.host.connection.run("mkdir -p {}/.staging".format(localRoot), timeout=3)
		writers = self.findWriters()
		capture_command = " ".join(shlex.quote(arg) for arg in [
			"sh", "-c", QUIESCE_CAPTURE_SCRIPT, "quiesce-capture",
			str(QUIESCE_CAPTURE_TIMEOUT), str(QUIESCE_WATCHDOG_SECONDS),
			self.name, localRoot, stagingPath,
		] + writers)
		try:
			result = self.host.connection.run(capture_command, hide=True, timeout=QUIESCE_CAPTURE_TIMEOUT + 30)
			print(result.stdout.strip(), flush=True)
			# Compress beside the staging copy and rename into place, so an interrupted
			# gzip never leaves a partial archive where tracking would count it.
			self.host.connection.run("docker run --rm --mount src={root},target={root},type=bind alpine:latest sh -c {script}".format(
				root=localRoot,
				script=shlex.quote('gzip -c "$0" > "$0.gz" && mv "$0.gz" "$1"') + " " + shlex.quote(stagingPath) + " " + shlex.quote(archivePath),
			), timeout=600)
		finally:
			try:
				self.host.connection.run("docker run --rm --mount src={root},target={root},type=bind alpine:latest rm -f {staging} {staging}.gz".format(
					root=localRoot,
					staging=stagingPath,
				), hide=True, timeout=60)
			except Exception as error:
				print("\033[93m** Warn ** Couldn't remove staging copy {}: {}\033[0m".format(stagingPath, error), flush=True)

	# Backs up the volume to all available hosts (except the one the volume is on)
	def backupToAll(self):
		# Local import to avoid circular dependency (host.py imports volume.py)
		from classes.host import Host
		(archive_path, date) = self.archiveLocally()
		failures = []
		for hostname in getHostsConfig():
			if hostname in self.data["skip_backup_on_hosts"]:
				print("Skipping {} (in skip_backup_on_hosts list) for {}".format(hostname, self.name), flush=True)
				continue
			target_host = Host(hostname)
			if target_host.domain != self.host.domain:
				try:
					target_path = target_host.backup_root + "host/{}/volume/".format(self.host.name)
					self.host.copyFileTo(archive_path, target_host, target_path)
				except Exception as e:
					print("Failed to copy {} to {}: {}".format(self.name, hostname, e), flush=True)
					failures.append((hostname, e))
		if failures:
			failed_hosts = ", ".join(h for h, _ in failures)
			raise Exception("backupToAll failed for {} host(s): {}".format(len(failures), failed_hosts))

	# Backs up the volume to all available hosts as an incremental, hardlink-rotated
	# rsync snapshot (ADR-0002).  Unlike backupToAll() there is no local tarball step:
	# rsync transfers directly from the live volume to each destination's dated
	# snapshot directory.
	def backupIncremental(self):
		# Local import to avoid circular dependency (host.py imports volume.py)
		from classes.host import Host
		date = datetime.today().strftime('%Y-%m-%d')
		failures = []
		for hostname in getHostsConfig():
			if hostname in self.data["skip_backup_on_hosts"]:
				print("Skipping {} (in skip_backup_on_hosts list) for {}".format(hostname, self.name), flush=True)
				continue
			target_host = Host(hostname)
			if target_host.domain != self.host.domain:
				try:
					self.host.rsyncVolumeSnapshot(self.name, target_host, date)
				except Exception as e:
					print("Failed to rsync {} to {}: {}".format(self.name, hostname, e), flush=True)
					failures.append((hostname, e))
		if failures:
			failed_hosts = ", ".join(h for h, _ in failures)
			raise Exception("backupIncremental failed for {} host(s): {}".format(len(failures), failed_hosts))

	def shouldBackup(self):
		if self.data["skip_backup"]:
			return False
		return True

	def backup(self):
		if not self.shouldBackup():
			return 0
		if self.backup_strategy == "incremental":
			# Quiescing would freeze the writers for the whole WAN rsync (#344).
			if self.quiesce:
				raise Exception("{} sets quiesce with backup_strategy incremental, which is not supported — refusing to back it up".format(self.name))
			self.backupIncremental()
		else:
			self.backupToAll()
		return 1

	def getData(self):
		return self.data

	@classmethod
	def getMissing(cls, volumes):
		missingVolumes = []
		for volumeName in getVolumesConfig():
			if not Volume.inList(volumeName, volumes):
				missingVolumes.append(volumeName)
		return missingVolumes

	@classmethod
	def inList(cls, volumeName, allVolumes):
		for volume in allVolumes:
			if volumeName == volume["name"]:
				return True
		return False