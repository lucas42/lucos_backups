'''
Backup
A set of snapshots of a given archive of files, copied from a particular `Host` across different points in time, stored on a given `Host`.
'''
from datetime import date, timedelta

class Backup:
	def __init__(self, stored_host, source_hostname, type, name):
		self.stored_host = stored_host
		self.source_hostname = source_hostname
		self.type = type
		self.name = name
		self.instances = []

	def addInstance(self, name, date, size, path):
		self.instances.append({
			'name': name,
			'date': date,
			'size': size,
			'path': path,
		})
		self.instances = sorted(self.instances, key=lambda i:i['date'])
	def getData(self):
		return {
			'source_host': self.source_hostname,
			'stored_host': self.stored_host.name,
			'is_local': self.source_hostname == self.stored_host.name,
			'type': self.type,
			'name': self.name,
			'earliest_date': min(self.instances, key=lambda i:i['date'])['date'],
			'latest_date': max(self.instances, key=lambda i:i['date'])['date'],
			'count': len(self.instances),
			'instances': self.instances,
		}
	def prune(self, dryrun=True):
		pruneCount = 0
		for instance in self.instances:
			if not self.toKeep(instance):
				if dryrun:
					# In dryrun mode, use `ls` to verify the file for deletion actually exists (will error if it doesn't)
					self.stored_host.connection.run("echo -n \"DRYRUN - would delete \" && ls {}".format(instance['path']), hide=False)
				else:
					self.stored_host.connection.run("rm -v {}".format(instance['path']), hide=False)
					pruneCount += 1
		return pruneCount

	'''
	Decides whether a given instance of the backup should be kept when pruning
	Newer backups are kept at a higher frequency than older ones
	Uses periods of 6 days to deliberately not align with weeks, to ensure restore points are available from various days of the week.
	'''
	def toKeep(self, instance):

		# Never prune a lone instance of a given backup (eg one-off files)
		if len(self.instances) == 1:
			return True

		age = date.today() - instance["date"]

		# For the first week, keep every instance
		if age < timedelta(weeks=1):
			return True
		# For the first 5 weeks, keep every sixth instance
		elif age < timedelta(weeks=5):
			return instance["date"].day % 6 == 0
		# For the first year, keep the instance from the sixth of each month
		elif agent < timedelta(weeks=52):
			return instance["date"].day == 6
		# After that, keep the instance from the sixth of January each year
		else:
			return instance["date"].day == 6 and instance["date"].month == 1
