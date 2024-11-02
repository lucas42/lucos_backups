'''
OneOffFile
Individual files stored for archival purposes, which are unlikely to be updated.
Backups are taken, but not snapshotted across different dates
'''
import os
from utils.config import getAllDomains

class OneOffFile:
	def __init__(self, host, path, modification_date, size):
		self.host = host
		self.filepath = path
		self.name = os.path.basename(path)
		self.modification_date = modification_date
		self.size = size

	def __str__(self):
		return "<One-Off File {} on {}>".format(self.name, self.host.name)

	# Backs up the volume to all available hosts (except the one the volume is on)
	def backupToAll(self):
		target_directory = "/srv/backups/host/{}/one-off/".format(self.host.name)
		for target_domain in getAllDomains(ignore=self.host):
			self.host.copyFileTo(self.filepath, target_domain, target_directory)

	def shouldBackup(self):
		return True

	def getData(self):
		return {
			'name': self.name,
			'source_host': self.host.name,
			'date': self.modification_date,
			'size': self.size,
		}