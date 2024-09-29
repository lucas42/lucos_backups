#! /usr/local/bin/python3
from loganne import loganneRequest
from schedule_tracker import updateScheduleTracker

# Record in loganne that the script has started
print ("\033[0mStarting Backups...")

try:
	#TODO: run backups
	print("\033[92m" + "// Backup logic not yet implemented" + "\033[0m")
except Exception as error:
	print ("\033[91m** Error ** " + str(error) + "\033[0m")

loganneRequest({
	"type":"backups",
	"humanReadable": "Backups Run",
})
updateScheduleTracker()