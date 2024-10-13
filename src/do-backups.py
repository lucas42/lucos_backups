#! /usr/local/bin/python3
import traceback
from loganne import loganneRequest
from schedule_tracker import updateScheduleTracker
from host import Host

# Record in loganne that the script has started
print ("\033[0mStarting Backups...")

try:
	volumeCount = 0
	for host in Host.getAll():
		print("Host:", host.domain)
		for volume in host.getVolumes():
			volume.backupToAll()
			volumeCount += 1
		host.closeConnection()
	print("\033[92m" + "Backups Complete" + "\033[0m")
except Exception as error:
	print ("\033[91m** Error ** " + str(error) + "\033[0m")
	traceback.print_exception(error)

loganneRequest({
	"type":"backups",
	"humanReadable": "{} volumes successfully backed up".format(volumeCount),
})
updateScheduleTracker()