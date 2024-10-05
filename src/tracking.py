import os, sys, io, json
import fabric, paramiko, yaml

if not os.environ.get("SSH_PRIVATE_KEY"):
	sys.exit("\033[91mSSH_PRIVATE_KEY not set\033[0m")

with open("volumes.yaml") as volume_file:
	config = yaml.safe_load(volume_file)
	volumesConfig = config["volumes"]
	effort_labels = config["effort_labels"]

def getPrivateKey():
	rawString = os.environ.get("SSH_PRIVATE_KEY").replace("~","=") # Padding characters are stored as tildas due to limitation in lucos_creds
	fileObject = io.StringIO(rawString)
	return paramiko.ed25519key.Ed25519Key.from_private_key(fileObject)

def fetchInfoByHost(host):
	conn = fabric.Connection(
		host=host,
		user="lucos-backups",
		connect_kwargs={
			"pkey": getPrivateKey(),
		},
	)
	result = conn.run('ls -l --time-style=long-iso --literal /srv/backups', hide=True)
	raw_files = result.stdout.splitlines()
	del raw_files[0] # Drop the header line from ls
	backups = []
	for file_info in raw_files:
		cols = file_info.split(maxsplit=7)
		backups.append({
			"name": cols[7],
			"date": cols[5],
		})
	volumes_result = conn.run('docker volume ls --format json', hide=True)
	raw_volumes = volumes_result.stdout.splitlines()
	volumes = []
	for volumejson in raw_volumes:
		volume = json.loads(volumejson)
		labels = volume["Labels"].split(",")
		volume["Labels"] = {}
		for label in labels:
			key, value = label.split("=", 1)
			volume["Labels"][key] = value
		if volume["Name"] in volumesConfig:
			volume["known"] = True
			volume["description"] = volumesConfig[volume["Name"]]["description"]
			volume["effort"] = volumesConfig[volume["Name"]]["effort"]
		else:
			volume["known"] = False
			volume["description"] = "Unknown Volume"
			volume["effort"] = "unknown"
		volume["effort label"] = effort_labels[volume["effort"]]
		volume["project link"] = "https://github.com/lucas42/"+volume['Labels']['com.docker.compose.project']
		volumes.append(volume)
	return {
		"backups": backups,
		"volumes": volumes,
	}

def fetchAllInfo():
	info = {
		"hosts": {
			"avalon": fetchInfoByHost("avalon.s.l42.eu"),
			"xwing": fetchInfoByHost("xwing.s.l42.eu"),
		},
		"notInConfig": [],
		"notOnHost": [],
	}
	allVolumes = info["hosts"]["avalon"]["volumes"] + info["hosts"]["xwing"]["volumes"]
	for volume in allVolumes:
		if not volume["known"]:
			info["notInConfig"].append(volume["Name"])
	for volumeName in volumesConfig:
		if not volumeInList(volumeName, allVolumes):
			info["notOnHost"].append(volumeName)
	return info

def volumeInList(volumeName, allVolumes):
	for volume in allVolumes:
		if volumeName == volume["Name"]:
			return True
	return False