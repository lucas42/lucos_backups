'''
Host
A particular computer (virutal or physical), which has files to be backed up from, can store backups, or both.
'''
import yaml, fabric
import os
from datetime import datetime
from classes.volume import Volume
from classes.backup import Backup

with open("config.yaml") as config_yaml:
	config = yaml.safe_load(config_yaml)

class Host:
	def __init__(self, name):
		self.name = name
		self.domain = config["hosts"][name]["domain"]
		self.connection = fabric.Connection(
			host=self.domain,
			user="lucos-backups",
			forward_agent=True,
		)

	def closeConnection(self):
		self.connection.close()

	def getVolumes(self):
		raw_volumes = self.connection.run('docker volume ls --format json', hide=True).stdout.splitlines()
		volumes = []
		for raw_volume in raw_volumes:
			volumes.append(Volume(self, raw_volume))
		return volumes

	def copyFileTo(self, source_path, target_host, target_path):
		print("Copying {} from {} to {} on {}".format(source_path, self.domain, target_path, target_host), flush=True)
		# Ensure the target directory exists
		self.connection.run('ssh -o StrictHostKeyChecking=no {} mkdir -p {}'.format(target_host, os.path.dirname(target_path)), hide=True)
		self.connection.run('scp {} {}:{}'.format(source_path, target_host, target_path), hide=True)

	def checkDiskSpace(self):
		raw_space_result = int(self.connection.run("df -P /srv/backups | tail -1 | awk '{print $4}'", hide=True).stdout.strip())
		readable_space_result = self.connection.run("df -Ph /srv/backups | tail -1 | awk '{print $4}'", hide=True).stdout.strip()
		percentage_space_result = int(self.connection.run("df -P /srv/backups | tail -1 | awk '{print $5}' | sed 's/%//'", hide=True).stdout.strip())
		return {
			'free_bytes': raw_space_result,
			'free_readable': readable_space_result,
			'used_percentage': percentage_space_result,
		}

	def checkBackupFiles(self):
		result = self.connection.run('ls -l --time-style=long-iso --literal /srv/backups', hide=True)
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

	def getVolumeBackups(self):
		rootDir = '/srv/backups/'
		filelist = self.connection.run("find {rootDir} -wholename '{rootDir}**/volume/*.*.tar.gz' -exec {exec} \\;".format(rootDir=rootDir, exec='du -sh {}'), hide=True).stdout.splitlines()
		backupList = []
		backups = {}
		for fileinfo in filelist:
			size, filepath = fileinfo.split('	', 1)
			directories = filepath.replace(rootDir, '').split('/')
			location = directories.pop(0) # Should either be local or host
			if location == 'host':
				source_hostname=directories.pop(0)
			else:
				source_hostname = self.name
			backup_type = directories.pop(0)
			filename = directories.pop()
			name, date, extension = filename.split('.', 2)
			if name not in backups:
				backups[name] = Backup(
					stored_host=self,
					source_hostname=source_hostname,
					type=backup_type,
					name=name,
				)
				backupList.append(backups[name])
			backups[name].addInstance(
				name=filename,
				date=datetime.strptime(date, '%Y-%m-%d').date(),
				size=size,
				path=filepath,
			)
		return backupList

	def getData(self):
		return {
			'domain': self.domain,
			'volumes': [vol.getData() for vol in self.getVolumes()],
			'disk': self.checkDiskSpace(),
			'backedup_volumes': sorted([backup.getData() for backup in self.getVolumeBackups()], key=lambda i:i['is_local'], reverse=True),
		}

	@classmethod
	def getAll(cls):
		hostlist = []
		for host in config["hosts"]:
			hostlist.append(cls(host))
		return hostlist


