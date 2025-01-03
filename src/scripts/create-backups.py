#! /usr/local/bin/python3
import traceback
from utils.loganne import loganneRequest
from utils.schedule_tracker import updateScheduleTracker
from classes.host import Host
from classes.repository import Repository

# Record in loganne that the script has started
print ("\033[0mStarting Backups...", flush=True)

try:
	backupCount = 0
	for host in Host.getAll():
		print("Host:", host.domain, flush=True)
		for volume in host.getVolumes():
			backupCount += volume.backup()
		for file in host.getOneOffFiles():
			backupCount += file.backup()
		host.closeConnection()
	for repo in Repository.getAll():
		backupCount += repo.backup()
	print("\033[92m" + "Backups Complete" + "\033[0m", flush=True)
	if backupCount > 0:
		loganneRequest({
			"type":"backups",
			"humanReadable": "{} archives successfully backed up".format(backupCount),
		})
	updateScheduleTracker()
except Exception as error:
	print ("\033[91m** Error ** " + str(error) + "\033[0m", flush=True)
	traceback.print_exception(error)
	updateScheduleTracker(success=False, message=str(error))
