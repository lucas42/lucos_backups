import datetime
import threading
from classes.host import Host
from classes.volume import Volume
from classes.repository import Repository
from schedule_tracker import updateScheduleTracker

RETRY_DELAY_SECONDS = 5 * 60  # Retry after 5 minutes if any host fails tracking
_retry_timer = None
latestInfo = None  # Populated by fetchAllInfo(); None until the first run completes

def fetchAllInfo():
	global _retry_timer
	# Cancel any pending retry — we're doing a fresh run now
	if _retry_timer is not None:
		_retry_timer.cancel()
		_retry_timer = None
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
			"hostsFailedTracking": {}
		}
		for host in Host.getAll():
			try:
				info["hosts"][host.name] = host.getData()
				info["volumes"] += info["hosts"][host.name]["volumes"]
				info["one_off_files"] += info["hosts"][host.name]["one_off_files"]
				info["backups"] += info["hosts"][host.name]["backups"]
			except Exception as error:
				info["hostsFailedTracking"][host] = str(error)
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
		# If any hosts failed, schedule an automatic retry so the service self-heals
		# without waiting for the next hourly cron run
		if info["hostsFailedTracking"]:
			_retry_timer = threading.Timer(RETRY_DELAY_SECONDS, fetchAllInfo)
			_retry_timer.daemon = True
			_retry_timer.start()
			print("\033[93mRetrying tracking in {} minutes due to {} host(s) failing\033[0m".format(
				RETRY_DELAY_SECONDS // 60,
				len(info["hostsFailedTracking"]),
			), flush=True)
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

# Start the first tracking run in a background thread so the HTTP server can
# start immediately and answer healthchecks while data is being fetched.
_initial_thread = threading.Thread(target=fetchAllInfo, daemon=True)
_initial_thread.start()