import yaml, fabric
import os
from volume import Volume

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
		print("Copying {} from {} to {} on {}".format(source_path, self.domain, target_path, target_host))
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

	def checkBackedUpVolumes(self):
		filepaths = self.connection.run('find /srv/backups/ -wholename \'/srv/backups/hosts/*/volumes/*.*.tar.gz\'', hide=True).stdout.splitlines()
		volumes = {}
		for filepath in filepaths:
			parts = filepath.split('/', 6)
			fileName_parts = parts[6].split('.', 2)
			source_host = parts[4]
			volume = fileName_parts[0]
			date = fileName_parts[1]
			if volume not in volumes:
				volumes[volume] = {}
			if source_host not in volumes[volume]:
				volumes[volume][source_host] = {
					'source_host': source_host,
					'stored_host': self.name,
					'volume': volume,
					'latest_date': date,
					'earliest_date': date,
					'count': 1,
				}
			else:
				volumes[volume][source_host]['count'] += 1
				if date > volumes[volume][source_host]['latest_date']:
					volumes[volume][source_host]['latest_date'] = date
				if date < volumes[volume][source_host]['earliest_date']:
					volumes[volume][source_host]['earliest_date'] = date
		volumeList = []
		for volume in volumes:
			for source_host in volumes[volume]:
				volumeList.append(volumes[volume][source_host])
		return volumeList

	def getData(self):
		return {
			'volumes': [vol.getData() for vol in self.getVolumes()],
			'disk': self.checkDiskSpace(),
			'backups': self.checkBackupFiles(),
			'backedup_volumes': self.checkBackedUpVolumes(),
		}

	@classmethod
	def getAll(cls):
		hostlist = []
		for host in config["hosts"]:
			hostlist.append(cls(host))
		return hostlist


