'''
Host
A particular computer (virutal or physical), which has files to be backed up from, can store backups, or both.
'''
import yaml, fabric, invoke
import os
from datetime import datetime
from classes.volume import Volume
from classes.backup import Backup
from classes.oneoff import OneOffFile
from utils.config import getHostsConfig

ROOT_DIR = '/srv/backups/'

class Host:
	def __init__(self, name):
		self.name = name
		self.domain = getHostsConfig()[name]["domain"]
		self.connection = fabric.Connection(
			host=self.domain,
			user="lucos-backups",
			forward_agent=True,
		)

	def closeConnection(self):
		self.connection.close()

	def getVolumes(self):
		raw_volumes = self.connection.run('docker volume ls --format json', hide=True, timeout=3).stdout.splitlines()
		volumes = []
		for raw_volume in raw_volumes:
			volumes.append(Volume(self, raw_volume))
		return volumes

	def getOneOffFiles(self):
		directory = "{ROOT_DIR}local/one-off/".format(ROOT_DIR=ROOT_DIR)
		self.connection.run("mkdir -p {directory}".format(directory=directory), timeout=3)
		self.connection.run("chmod g+w {directory}".format(directory=directory), timeout=3) # Allow any user in the group to add files to the one-off directory
		filelist = []
		raw_files = self.connection.run("ls -l --human-readable --time-style=long-iso --literal {directory}".format(directory=directory), hide=True, timeout=3).stdout.splitlines()
		del raw_files[0] # Drop the header line from ls
		for file_info in raw_files:
			cols = file_info.split(maxsplit=7)
			filelist.append(OneOffFile(
				host=self,
				path=directory+cols[7],
				modification_date=cols[5],
				size=cols[4],
			))
		return filelist

	def copyFileTo(self, source_path, target_host, target_path):
		print("Copying {} from {} to {} on {}".format(source_path, self.domain, target_path, target_host), flush=True)
		# Ensure the target directory exists
		self.connection.run('ssh -o StrictHostKeyChecking=no {} mkdir -p {}'.format(target_host, os.path.dirname(target_path)), hide=True, timeout=3)
		self.connection.run('scp "{}" {}:"{}"'.format(source_path, target_host, target_path), hide=True)

	def fileExistsRemotely(self, target_host, target_directory, target_filename):
		try:
			self.connection.run('ssh -o StrictHostKeyChecking=no {} \'ls -p "{}"\''.format(target_host, target_directory+target_filename), hide=True, timeout=3)
			return True
		except invoke.exceptions.UnexpectedExit as e:
			return False

	def checkDiskSpace(self):
		raw_space_result = int(self.connection.run("df -P /srv/backups | tail -1 | awk '{print $4}'", hide=True, timeout=3).stdout.strip())
		readable_space_result = self.connection.run("df -Ph /srv/backups | tail -1 | awk '{print $4}'", hide=True, timeout=3).stdout.strip()
		percentage_space_result = int(self.connection.run("df -P /srv/backups | tail -1 | awk '{print $5}' | sed 's/%//'", hide=True, timeout=3).stdout.strip())
		return {
			'free_bytes': raw_space_result,
			'free_readable': readable_space_result,
			'used_percentage': percentage_space_result,
		}

	def checkBackupFiles(self):
		result = self.connection.run('ls -l --time-style=long-iso --literal /srv/backups', hide=True, timeout=3)
		raw_files = result.stdout.splitlines()
		del raw_files[0] # Drop the header line from ls
		backup_files = []
		for file_info in raw_files:
			cols = file_info.split(maxsplit=7)
			backup_files.append({
				"name": cols[7],
				"date": cols[5],
			})
		return backup_files

	def getBackups(self):
		filelist = self.connection.run("find {ROOT_DIR} -wholename '{ROOT_DIR}*/**' -type f -printf \"%TY-%Tm-%Td\t\" -exec {exec}".format(ROOT_DIR=ROOT_DIR, exec='du -sh {} \\;'), hide=True, timeout=3).stdout.splitlines()
		backupList = []
		backups = {}
		for fileinfo in filelist:
			mod_date, size, filepath = fileinfo.split('	', 2)
			directories = filepath.replace(ROOT_DIR, '').split('/')
			location = directories.pop(0) # Should either be local or host
			if location == 'host':
				source_hostname=directories.pop(0)
			elif location == 'local':
				source_hostname = self.name
			elif location == 'external':
				source_hostname=directories.pop(0)
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


