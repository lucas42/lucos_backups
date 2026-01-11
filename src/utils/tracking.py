import datetime
from classes.host import Host
from classes.volume import Volume
from classes.repository import Repository
from utils.schedule_tracker import updateScheduleTracker

def fetchAllInfo():
	print ("\033[0mTracking Backups...", flush=True)
	try:
		info = {
			"hosts": {},
			"volumes": [],
			"one_off_files": [],
			"notInConfig": [],
			"notOnHost": [],
			"backups": [],
			"repositories": [],
			"hostsFailedTracking": []
		}
		for host in Host.getAll():
			try:
				info["hosts"][host.name] = host.getData()
				info["volumes"] += info["hosts"][host.name]["volumes"]
				info["one_off_files"] += info["hosts"][host.name]["one_off_files"]
				info["backups"] += info["hosts"][host.name]["backups"]
			except Exception as error:
				info["hostsFailedTracking"].append(host)
		info["repositories"] = [repo.getData() for repo in Repository.getAll()]
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
		for repo in info["repositories"]:
			repo["backups"] = []
			for backup in info["backups"]:
				if backup['type'] == "repository" and repo["name"] == backup["name"]:
					repo["backups"].append(backup)

		info["notOnHost"] = Volume.getMissing(info["volumes"])
		info["update_time"] = datetime.datetime.now(datetime.timezone.utc)

		# Only updates the global variable once all info is fetched
		global latestInfo
		latestInfo = info

		updateScheduleTracker(
			system="lucos_backups_tracking",
			success=True,
			frequency=60*60, # 1 hour in seconds
		)
		print("\033[92m" + "Tracking completed successfully" + "\033[0m", flush=True)
	except Exception as error:
		print ("\033[91m** Error ** " + str(error) + "\033[0m", flush=True)
		updateScheduleTracker(
			system="lucos_backups_tracking",
			success=False,
			message=str(error),
			frequency=60*60, # 1 hour in seconds
		)
		raise error

def getAllInfo():
	return latestInfo

fetchAllInfo()