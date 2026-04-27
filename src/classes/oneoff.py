'''
OneOffFile
Individual files stored for archival purposes, which are unlikely to be updated.
Backups are taken, but not snapshotted across different dates
'''
import os
from utils.config import getHostsConfig

class OneOffFile:
	def __init__(self, host, path, modification_date, size):
		self.host = host
		self.filepath = path
		self.name = os.path.basename(path)
		self.modification_date = modification_date
		self.size = size

	def __str__(self):
		return "<One-Off File {} on {}>".format(self.name, self.host.name)

	def backup(self):
		# Local import to avoid circular dependency (host.py imports oneoff.py)
		from classes.host import Host
		backupMade = False
		for hostname in getHostsConfig():
			target_host = Host(hostname)
			if target_host.domain == self.host.domain:
				continue
			target_directory = target_host.backup_root + "host/{}/one-off/".format(self.host.name)
			if self.host.fileExistsRemotely(target_host, target_directory, self.name):
				continue
			self.host.copyFileTo(self.filepath, target_host, target_directory)
			backupMade = True
		if backupMade:
			return 1
		else:
			return 0

	def getData(self):
		return {
			'name': self.name,
			'source_host': self.host.name,
			'date': self.modification_date,
			'size': self.size,
		}
