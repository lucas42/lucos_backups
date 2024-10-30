#! /usr/local/bin/python3
import traceback
from utils.loganne import loganneRequest
from utils.schedule_tracker import updateScheduleTracker
from classes.host import Host

print ("\033[0mPruning Backups...", flush=True)
pruneCount = 0
try:
	for host in Host.getAll():
		print("Host: {}".format(host.domain))
		for backup in host.getVolumeBackups():
			print("	Volume {} from {} - has {} instances".format(backup.name, backup.source_hostname, len(backup.instances)))
			numberPruned = backup.prune(dryrun=False)
			if numberPruned > 0:
				print("		{} instances deleted".format(numberPruned))
			pruneCount += numberPruned
		host.closeConnection()
	print("\033[92mPruning Complete - {} backups pruned\033[0m".format(pruneCount), flush=True)
	if pruneCount > 0:
		loganneRequest({
			"type":"prune-backups",
			"humanReadable": "{} backups pruned".format(pruneCount),
		})
	updateScheduleTracker(system="lucos_backups_prune")
except Exception as error:
	print ("\033[91m** Error ** " + str(error) + "\033[0m", flush=True)
	traceback.print_exception(error)
	updateScheduleTracker(system="lucos_backups_prune", success=False, message=str(error))
