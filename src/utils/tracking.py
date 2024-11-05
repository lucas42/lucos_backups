import datetime
from classes.host import Host
from classes.volume import Volume
from classes.repository import Repository

def fetchAllInfo():
	info = {
		"hosts": {},
		"volumes": [],
		"one_off_files": [],
		"notInConfig": [],
		"notOnHost": [],
		"backups": [],
		"repositories": [],
	}
	for host in Host.getAll():
		info["hosts"][host.name] = host.getData()
		info["volumes"] += info["hosts"][host.name]["volumes"]
		info["one_off_files"] += info["hosts"][host.name]["one_off_files"]
		info["backups"] += info["hosts"][host.name]["backups"]
	for volume in info["volumes"]:
		if not volume["known"]:
			info["notInConfig"].append(volume["name"])
		volume["backups"] = []
		for backup in info["backups"]:
			if backup['type'] == "volume" and volume["name"] == backup["name"] and volume["source_host"] == backup["source_host"]:
				volume["backups"].append(backup)
	for file in info["one_off_files"]:
		file["backups"] = []
		for backup in info["backups"]:
			if backup['type'] == "one-off" and file["name"] == backup["name"] and file["source_host"] == backup["source_host"]:
				file["backups"].append(backup)

	info["notOnHost"] = Volume.getMissing(info["volumes"])
	info["update_time"] = datetime.datetime.now(datetime.timezone.utc)

	info["repositories"] = [repo.getData() for repo in Repository.getAll()]

	# Only updates the global variable once all info is fetched
	global latestInfo
	latestInfo = info

def getAllInfo():
	return latestInfo

fetchAllInfo()