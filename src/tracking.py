import datetime
from host import Host
from volume import Volume

def fetchAllInfo():
	info = {
		"hosts": {},
		"volumes": [],
		"notInConfig": [],
		"notOnHost": [],
	}
	for host in Host.getAll():
		info["hosts"][host.name] = host.getData()
		info["volumes"] += info["hosts"][host.name]["volumes"]
	for volume in info["volumes"]:
		if not volume["known"]:
			info["notInConfig"].append(volume["name"])
	info["notOnHost"] = Volume.getMissing(info["volumes"])
	info["update_time"] = datetime.datetime.now(datetime.timezone.utc)

	# Only updates the global variable once all info is fetched
	global latestInfo
	latestInfo = info

def getAllInfo():
	return latestInfo

fetchAllInfo()