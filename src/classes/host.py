'''
Host
A particular computer (virtual or physical), which has files to be backed up from, can store backups, or both.
'''
import yaml, fabric, invoke
import os
from datetime import datetime
from classes.volume import Volume
from classes.backup import Backup
from classes.oneoff import OneOffFile
from classes.shell import GnuShell, BusyBoxShell
from utils.config import getHostsConfig

# The OS user every host's lucos-backups SSH connection runs as (see the
# fabric.Connection(user="lucos-backups") calls below).  When commands run
# directly on a host they default to this user implicitly; but the incremental
# rsync runs ssh from *inside a container* (as root), so the user must be stated
# explicitly on the rsync target and the ProxyJump host.
SSH_USER = "lucos-backups"

# The source host's lucos-backups known_hosts.  The incremental rsync runs ssh
# from inside an ephemeral container (as root) which has no known_hosts of its
# own, so the ProxyJump gateway and target host keys can't be verified and the
# jump hop fails host-key verification (#327) — note that command-line
# StrictHostKeyChecking=no does NOT propagate to the ProxyJump hop.  We mount
# this file read-only into the container so its ssh verifies the same host keys
# the host-side scp path already trusts.  init-host.sh creates the user with
# `useradd --system --create-home`, so the home is /home/lucos-backups.
SSH_KNOWN_HOSTS = "/home/{}/.ssh/known_hosts".format(SSH_USER)

def backup_image_ref():
	'''The lucos_backups image used to deliver source-side tooling (rsync) for the
	incremental strategy — pinned to this container's own VERSION so the tooling
	is the exact version CI built and published alongside this code.  Falls back
	to :latest in development where VERSION is unset.'''
	version = os.environ.get("VERSION") or "latest"
	return "lucas42/lucos_backups:{}".format(version)

def format_bytes(size_bytes):
	'''Convert a byte count to a human-readable string (e.g. 1.2G, 340M)'''
	size_bytes = int(size_bytes)
	for unit in ['', 'K', 'M', 'G', 'T']:
		if abs(size_bytes) < 1024.0:
			return "{:.1f}{}".format(size_bytes, unit)
		size_bytes /= 1024.0
	return "{:.1f}P".format(size_bytes)

class Host:
	def __init__(self, name):
		self.name = name
		host_config = getHostsConfig()[name]
		self.domain = host_config["domain"]
		self.is_storage_only = host_config.get("is_storage_only") or False
		self.backup_root = host_config.get("backup_root") or "/srv/backups/"
		self.ssh_gateway = host_config.get("ssh_gateway")
		# can_reach_external_services: whether this host can wget/curl from public
		# HTTPS endpoints (e.g. GitHub codeload).  Distinct from is_storage_only
		# (which means "no docker volumes of its own").  The configy API always
		# returns an explicit boolean (defaulting True when absent from YAML).
		self.can_reach_external_services = host_config.get("can_reach_external_services", True)

		if self.ssh_gateway:
			self.ssh_gateway_domain = getHostsConfig()[self.ssh_gateway]["domain"]
			self.gateway = fabric.Connection(
				host=self.ssh_gateway_domain,
				user="lucos-backups",
				forward_agent=True,
			)
			self.connection = fabric.Connection(
				host=self.domain,
				user="lucos-backups",
				forward_agent=True,
				gateway=self.gateway,
			)
		else:
			self.ssh_gateway_domain = None
			self.gateway = None
			self.connection = fabric.Connection(
				host=self.domain,
				user="lucos-backups",
				forward_agent=True,
			)

		shell_flavour = host_config.get("shell_flavour") or "gnu"
		if shell_flavour == "busybox":
			self.shell = BusyBoxShell(self.connection, self.backup_root)
		else:
			self.shell = GnuShell(self.connection, self.backup_root)

	def closeConnection(self):
		try:
			self.connection.close()
		finally:
			if self.gateway:
				self.gateway.close()

	def getVolumes(self):
		if self.is_storage_only:
			return []
		raw_volumes = self.connection.run('docker volume ls --format json', hide=True, timeout=10).stdout.splitlines()
		volumes = []
		for raw_volume in raw_volumes:
			try:
				volumes.append(Volume(self, raw_volume))
			except Exception as error:
				print("\033[91m** Error ** Skipping volume on {}: {}\033[0m".format(self.domain, error), flush=True)
		return volumes

	def getOneOffFiles(self):
		if self.is_storage_only:
			return []
		directory = "{backup_root}local/one-off/".format(backup_root=self.backup_root)
		self.shell.ensure_one_off_dir(directory)
		return [
			OneOffFile(
				host=self,
				path=f['path'],
				modification_date=f['modification_date'],
				size=f['size'],
			)
			for f in self.shell.list_one_off_files(directory)
		]

	def _outbound_ssh_args(self, target_host):
		'''Build SSH option flags for outbound connections to target_host.

		If `self` IS the gateway for the target host, the ProxyJump flag is
		omitted: there is no point asking xwing to ProxyJump through xwing
		to reach aurora — the recursive connection fails with SSH error 255.
		In that case xwing connects directly to aurora's domain (which it
		can reach on the LAN since it is the gateway by definition).'''
		args = ['-o', 'StrictHostKeyChecking=no']
		if target_host.ssh_gateway and target_host.ssh_gateway_domain != self.domain:
			args += ['-o', 'ProxyJump={}'.format(target_host.ssh_gateway_domain)]
		return args

	def runOnRemote(self, target_host, command, timeout=10):
		'''Run a command on target_host via SSH, routing through a gateway if configured.'''
		ssh_args = ' '.join(self._outbound_ssh_args(target_host))
		return self.connection.run(
			'ssh {} {} {}'.format(ssh_args, target_host.domain, command),
			hide=True, timeout=timeout,
		)

	def _container_ssh_command(self, target_host):
		'''The ssh command rsync uses (via --rsh) when run *inside a container*.

		Differs from _outbound_ssh_args in two ways: it returns a ready-to-use
		command string (not arg list), and it qualifies the ProxyJump host with
		the SSH user — inside the container ssh runs as root, so it cannot rely on
		the implicit lucos-backups OS user that a host-side ssh would default to.

		StrictHostKeyChecking=no is set here as parity with the host-side path:
		_outbound_ssh_args (used by the existing scp/runOnRemote to aurora) already
		sets it, so the whole cross-host backup SSH path runs this way — known keys
		are verified, unknown keys are accepted on first use. The container has no
		known_hosts of its own, so rsyncVolumeSnapshot mounts the host user's
		known_hosts (SSH_KNOWN_HOSTS) into it; without that the ProxyJump hop fails
		host-key verification, because StrictHostKeyChecking=no does not propagate
		to the jump connection (#327).'''
		args = ['ssh', '-o', 'StrictHostKeyChecking=no']
		if target_host.ssh_gateway and target_host.ssh_gateway_domain != self.domain:
			args += ['-o', 'ProxyJump={}@{}'.format(SSH_USER, target_host.ssh_gateway_domain)]
		return ' '.join(args)

	def _latest_snapshot_date(self, target_host, snapshot_base, exclude_date):
		'''Return the most recent dated snapshot directory name under snapshot_base
		on target_host (for use as rsync --link-dest), or None if there are none.
		Ignores exclude_date (today) and any non-date entries (e.g. *.partial).'''
		try:
			result = self.runOnRemote(target_host, "ls -1 {}".format(snapshot_base))
		except invoke.exceptions.UnexpectedExit:
			return None  # base dir doesn't exist yet (first ever snapshot)
		dates = []
		for entry in result.stdout.splitlines():
			entry = entry.strip()
			if entry == exclude_date:
				continue
			try:
				datetime.strptime(entry, '%Y-%m-%d')
			except ValueError:
				continue  # skip .partial dirs and anything not a plain date
			dates.append(entry)
		return max(dates) if dates else None

	def rsyncVolumeSnapshot(self, volume_name, target_host, date):
		'''Incremental backup of a docker volume to target_host as a dated,
		hardlink-rotated snapshot directory (ADR-0002).

		rsync runs inside a container on *this* (source) host — same delivery
		pattern as archiveLocally()'s tar — so nothing is installed on the host.
		The container is given the forwarded SSH agent socket so it can reach the
		target (through the ProxyJump gateway) using the same keys the host-side
		scp path uses, and the host user's known_hosts (mounted read-only) so it
		can verify the gateway and target host keys (#327).

		--link-dest against the previous snapshot makes unchanged files hardlinks
		(retention ≈ one full + deltas).  --partial --append-verify makes an
		interrupted transfer resumable.  The transfer lands in <date>.partial and
		is renamed to <date> only on success, so a torn copy can never read back
		as a valid snapshot (ADR C4).'''
		snapshot_base = target_host.backup_root + "host/{}/volume-snapshots/{}/".format(self.name, volume_name)
		partial_path = "{}{}.partial".format(snapshot_base, date)
		final_path = "{}{}".format(snapshot_base, date)

		self.runOnRemote(target_host, "mkdir -p {}".format(snapshot_base))
		previous = self._latest_snapshot_date(target_host, snapshot_base, exclude_date=date)
		link_dest = " --link-dest={}{}/".format(snapshot_base, previous) if previous else ""

		rsync_command = (
			'docker run --rm '
			'--volume {volume_name}:/raw-data:ro '
			'--volume "$SSH_AUTH_SOCK":/ssh-agent '
			'--volume {known_hosts}:/root/.ssh/known_hosts:ro '
			'--env SSH_AUTH_SOCK=/ssh-agent '
			'{image} '
			'rsync --archive --numeric-ids --partial --append-verify --delete{link_dest} '
			'--rsh "{rsh}" '
			'/raw-data/ {user}@{target_domain}:"{partial}/"'
		).format(
			volume_name=volume_name,
			image=backup_image_ref(),
			known_hosts=SSH_KNOWN_HOSTS,
			link_dest=link_dest,
			rsh=self._container_ssh_command(target_host),
			user=SSH_USER,
			target_domain=target_host.domain,
			partial=partial_path,
		)
		print("Rsyncing snapshot of {} from {} to {} on {}".format(volume_name, self.domain, final_path, target_host.name), flush=True)
		self.connection.run(rsync_command, hide=True, timeout=7200)

		# Atomic publish: replace any previous same-day snapshot, then rename the
		# completed partial into place.  The .partial guarantees a torn transfer
		# is never seen as a finished snapshot.  Normally <final> doesn't exist (a
		# new date), so rm is a no-op and mv is instant; the longer timeout only
		# matters on a same-day re-run where rm -rf clears a large prior snapshot.
		self.runOnRemote(target_host, 'rm -rf "{final}" && mv "{partial}" "{final}"'.format(final=final_path, partial=partial_path), timeout=600)

	def copyTo(self, target_host, source, dest):
		'''Copy a file to target_host via SCP, routing through a gateway if configured.
		timeout=7200 (2 hours) gives large volumes (e.g. 6.6 GB lucos_photos_photos) enough
		wall-clock time to complete while still providing a safety valve against hung connections.
		The original 600s cap was too low for that volume.'''
		ssh_args = ' '.join(self._outbound_ssh_args(target_host))
		self.connection.run(
			'scp {} "{}" {}:"{}"'.format(ssh_args, source, target_host.domain, dest),
			hide=True,
			timeout=7200,
		)

	def copyFileTo(self, source_path, target_host, target_path):
		print("Copying {} from {} to {} on {}".format(source_path, self.domain, target_path, target_host.name), flush=True)
		self.runOnRemote(target_host, 'mkdir -p {}'.format(os.path.dirname(target_path)))
		self.copyTo(target_host, source_path, target_path)

	def fileExistsRemotely(self, target_host, target_directory, target_filename):
		try:
			self.runOnRemote(target_host, 'ls -p "{}"'.format(target_directory + target_filename))
			return True
		except invoke.exceptions.UnexpectedExit:
			return False

	def checkDiskSpace(self):
		return self.shell.disk_space()

	def checkBackupFiles(self):
		return self.shell.list_backup_dir()

	def getBackups(self):
		filelist = self.shell.find_backup_files()
		backupList = []
		backups = {}
		for fileinfo in filelist:
			mod_date, size_bytes, filepath = fileinfo.split('\t', 2)
			size = format_bytes(size_bytes)
			directories = filepath.replace(self.backup_root, '').split('/')
			location = directories.pop(0)  # local, host, or external
			if location == 'host':
				source_hostname = directories.pop(0)
			elif location == 'local':
				source_hostname = self.name
			elif location == 'external':
				source_hostname = directories.pop(0)
			backup_type = directories.pop(0)
			filename = directories.pop()
			if backup_type == 'volume' or backup_type == 'repository':
				name, raw_date, archive_ext, compression_ext = filename.rsplit('.', 3)
				try:
					date = datetime.strptime(raw_date, '%Y-%m-%d').date()
				except Exception as error:
					print("\033[91m** Warn ** {} File: {}\033[0m".format(error, filepath), flush=True)
					date = datetime.strptime(mod_date, '%Y-%m-%d').date()
			else:
				name = filename
				date = datetime.strptime(mod_date, '%Y-%m-%d').date()
			key = source_hostname + "/" + name
			if key not in backups:
				backups[key] = Backup(
					stored_host=self,
					source_hostname=source_hostname,
					type=backup_type,
					name=name,
				)
				backupList.append(backups[key])
			backups[key].addInstance(
				name=filename,
				date=date,
				size=size,
				path=filepath,
			)
		return backupList + self.getSnapshotBackups()

	def getSnapshotBackups(self):
		'''Discover incremental (rsync --link-dest) snapshot backups stored on this
		host.  Each dated directory under host/<src>/volume-snapshots/<vol>/<date>
		is one instance of a `volume-snapshot` backup.  Returned as recursive
		Backups so prune() removes whole directories.'''
		backups = {}
		backupList = []
		for snapshot_path in self.shell.find_snapshot_dirs():
			parts = snapshot_path.replace(self.backup_root, '', 1).strip('/').split('/')
			# Expected layout: host / <source_host> / volume-snapshots / <volume> / <date>
			if len(parts) != 5 or parts[0] != 'host' or parts[2] != 'volume-snapshots':
				continue
			source_hostname, volume_name, raw_date = parts[1], parts[3], parts[4]
			try:
				date = datetime.strptime(raw_date, '%Y-%m-%d').date()
			except ValueError:
				continue  # skip *.partial and any non-date entries
			key = source_hostname + "/" + volume_name
			if key not in backups:
				backups[key] = Backup(
					stored_host=self,
					source_hostname=source_hostname,
					type='volume-snapshot',
					name=volume_name,
					recursive=True,
				)
				backupList.append(backups[key])
			# Size of a hardlinked snapshot tree is misleading (shared inodes) and
			# expensive to compute remotely, so it's left as a placeholder — the
			# host's overall disk-space section is the meaningful capacity signal.
			backups[key].addInstance(
				name=raw_date,
				date=date,
				size="—",
				path=snapshot_path,
			)
		return backupList

	def getData(self):
		try:
			return {
				'domain': self.domain,
				'volumes': [vol.getData() for vol in self.getVolumes()],
				'one_off_files': [file.getData() for file in self.getOneOffFiles()],
				'disk': self.checkDiskSpace(),
				'backups': sorted([backup.getData() for backup in self.getBackups()], key=lambda i:i['is_local'], reverse=True),
			}
		except Exception as error:
			print("\033[91m** Error ** Problem retrieving data from {}: {}\033[0m".format(self.domain, error), flush=True)
			raise error

	@classmethod
	def getAll(cls):
		hostlist = []
		for host in getHostsConfig():
			hostlist.append(cls(host))
		return hostlist
