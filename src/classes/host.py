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
		self.is_storage_only = host_config.get("is_storage_only", False)
		self.backup_root = host_config.get("backup_root", "/srv/backups/")
		self.ssh_gateway = host_config.get("ssh_gateway")

		if self.ssh_gateway:
			self.ssh_gateway_domain = getHostsConfig()[self.ssh_gateway]["domain"]
			gateway = fabric.Connection(
				host=self.ssh_gateway_domain,
				user="lucos-backups",
				forward_agent=True,
			)
			self.connection = fabric.Connection(
				host=self.domain,
				user="lucos-backups",
				forward_agent=True,
				gateway=gateway,
			)
		else:
			self.ssh_gateway_domain = None
			self.connection = fabric.Connection(
				host=self.domain,
				user="lucos-backups",
				forward_agent=True,
			)

		shell_flavour = host_config.get("shell_flavour", "gnu")
		if shell_flavour == "busybox":
			self.shell = BusyBoxShell(self.connection, self.backup_root)
		else:
			self.shell = GnuShell(self.connection, self.backup_root)

	def closeConnection(self):
		self.connection.close()

	def getVolumes(self):
		if self.is_storage_only:
			return []
		raw_volumes = self.connection.run('docker volume ls --format json', hide=True, timeout=10).stdout.splitlines()
		volumes = []
		for raw_volume in raw_volumes:
			volumes.append(Volume(self, raw_volume))
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
		'''Build SSH option flags for outbound connections to target_host.'''
		args = ['-o', 'StrictHostKeyChecking=no']
		if target_host.ssh_gateway:
			args += ['-o', 'ProxyJump={}'.format(target_host.ssh_gateway_domain)]
		return args

	def runOnRemote(self, target_host, command):
		'''Run a command on target_host via SSH, routing through a gateway if configured.'''
		ssh_args = ' '.join(self._outbound_ssh_args(target_host))
		self.connection.run(
			'ssh {} {} {}'.format(ssh_args, target_host.domain, command),
			hide=True, timeout=10,
		)

	def copyTo(self, target_host, source, dest):
		'''Copy a file to target_host via SCP, routing through a gateway if configured.'''
		ssh_args = ' '.join(self._outbound_ssh_args(target_host))
		self.connection.run(
			'scp {} "{}" {}:"{}"'.format(ssh_args, source, target_host.domain, dest),
			hide=True,
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
