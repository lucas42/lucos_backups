import datetime
from classes.host import Host
from classes.volume import Volume

def fetchAllInfo():
	info = {
		"hosts": {},
		"volumes": [],
		"notInConfig": [],
		"notOnHost": [],
		"backedup_volumes": [],
	}
	for host in Host.getAll():
		info["hosts"][host.name] = host.getData()
		info["volumes"] += info["hosts"][host.name]["volumes"]
		info["backedup_volumes"] += info["hosts"][host.name]["backedup_volumes"]
	for volume in info["volumes"]:
		if not volume["known"]:
			info["notInConfig"].append(volume["name"])
		volume["backups"] = []
		for backup in info["backedup_volumes"]:
			if backup['type'] == "volume" and volume["name"] == backup["name"] and volume["source_host"] == backup["source_host"]:
				volume["backups"].append(backup)

	info["notOnHost"] = Volume.getMissing(info["volumes"])
	info["update_time"] = datetime.datetime.now(datetime.timezone.utc)

	# Only updates the global variable once all info is fetched
	global latestInfo
	latestInfo = info

def getAllInfo():
	return latestInfo

fetchAllInfo()