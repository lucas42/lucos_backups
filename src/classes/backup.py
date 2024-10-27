'''
Backup
A set of snapshots of a given archive of files, copied from a particular `Host` across different points in time, stored on a given `Host`.
'''
from datetime import datetime


class Backup:
	def __init__(self, stored_host, source_hostname, type, name):
		self.stored_host = stored_host
		self.source_hostname = source_hostname
		self.type = type
		self.name = name
		self.instances = []

	def addInstance(self, name, date, size):
		self.instances.append({
			'name': name,
			'date': date,
			'size': size,
		})
		self.instances = sorted(self.instances, key=lambda i:i['date'])
	def getData(self):
		return {
			'source_host': self.source_hostname,
			'stored_host': self.stored_host.name,
			'type': self.type,
			'name': self.name,
			'earliest_date': min(self.instances, key=lambda i:i['date'])['date'],
			'latest_date': max(self.instances, key=lambda i:i['date'])['date'],
			'count': len(self.instances),
			'instances': self.instances,
		}