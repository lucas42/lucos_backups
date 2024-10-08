#! /usr/local/bin/python3
import traceback
from loganne import loganneRequest
from schedule_tracker import updateScheduleTracker
from host import Host

# Record in loganne that the script has started
print ("\033[0mStarting Backups...")

try:
	print("\033[92m" + "// Backup logic not yet implemented" + "\033[0m")
	for host in Host.getAll():
		print("Host:", host.domain)
		for volume in host.getVolumes():
			if volume.shouldBackup():
				volume.backupTo("xwing.s.l42.eu")
except Exception as error:
	print ("\033[91m** Error ** " + str(error) + "\033[0m")
	traceback.print_exception(error)

loganneRequest({
	"type":"backups",
	"humanReadable": "Backups Run",
})
updateScheduleTracker()