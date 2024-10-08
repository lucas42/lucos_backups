import yaml
from connections import getConnection
from volume import Volume

with open("config.yaml") as config_yaml:
	config = yaml.safe_load(config_yaml)

class Host:
	def __init__(self, name):
		self.name = name
		self.domain = config["hosts"][name]["domain"]
		self.connection = getConnection(self.domain)

	def closeConnection(self):
		self.connection.close()

	def getVolumes(self):
		raw_volumes = self.connection.run('docker volume ls --format json', hide=True).stdout.splitlines()
		volumes = []
		for raw_volume in raw_volumes:
			volumes.append(Volume(self, raw_volume, config["volumes"], config["effort_labels"]))
		return volumes

	def copyFileTo(self, path, target_host):
		print("//TODO: Copy {} from {} to {}".format(path, self.domain, target_host))
		self.connection.run('ls -al {}'.format(path))

	@classmethod
	def getAll(cls):
		hostlist = []
		for host in config["hosts"]:
			hostlist.append(cls(host))
		return hostlist


