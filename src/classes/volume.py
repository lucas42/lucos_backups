'''
Volume
A particular docker volume stored on given `Host`
'''
import yaml
import json
from datetime import datetime
from utils.config import getVolumesConfig, getHostsConfig

with open("effort_labels.yaml") as effort_labels_yaml:
	effort_labels = yaml.safe_load(effort_labels_yaml)

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
		else:
			known = False
			description = "Unknown Volume"
			effort_id = "unknown"
			skip_backup = False
		labels =  {}
		for label in data["Labels"].split(","):
			key, value = label.split("=", 1)
			labels[key] = value
		try:
			project = labels['com.docker.compose.project']
		except:
			raise Exception("Project label missing from volume "+self.name)

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
		archiveDirectory = "/srv/backups/local/volume"
		date = datetime.today().strftime('%Y-%m-%d')
		archivePath = "{archive_directory}/{volume_name}.{date}.tar.gz".format(archive_directory=archiveDirectory, volume_name=self.name, date=date)
		self.host.connection.run("mkdir -p {}".format(archiveDirectory))
		self.host.connection.run("docker run --rm --volume {volume_name}:/raw-data --mount src={archive_directory},target={archive_directory},type=bind alpine:latest tar -C /raw-data -czf {archive_path} .".format(
			volume_name=self.name,
			archive_directory=archiveDirectory,
			archive_path=archivePath,
		))
		return (archivePath, date)

	# Backs up the volume to all available hosts (except the one the volume is on)
	def backupToAll(self):
		(archive_path, date) = self.archiveLocally()
		target_path = "/srv/backups/host/{}/volume/".format(self.host.name, self.name, date)
		for hostname in getHostsConfig():
			target_domain = getHostsConfig()[hostname]["domain"]
			if target_domain != self.host.domain:
				self.host.copyFileTo(archive_path, target_domain, target_path)

	def shouldBackup(self):
		return not self.data["skip_backup"]

	def backup(self):
		if self.shouldBackup():
			self.backupToAll()
			return 1
		else:
			return 0

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