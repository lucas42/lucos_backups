#! /usr/local/bin/python3
import traceback
from utils.loganne import loganneRequest
from utils.schedule_tracker import updateScheduleTracker
from classes.host import Host

# Record in loganne that the script has started
print ("\033[0mStarting Backups...", flush=True)

try:
	volumeCount = 0
	for host in Host.getAll():
		print("Host:", host.domain, flush=True)
		for volume in host.getVolumes():
			if volume.shouldBackup():
				volume.backupToAll()
				volumeCount += 1
		host.closeConnection()
	print("\033[92m" + "Backups Complete" + "\033[0m", flush=True)
except Exception as error:
	print ("\033[91m** Error ** " + str(error) + "\033[0m", flush=True)
	traceback.print_exception(error)

loganneRequest({
	"type":"backups",
	"humanReadable": "{} volumes successfully backed up".format(volumeCount),
})
updateScheduleTracker()