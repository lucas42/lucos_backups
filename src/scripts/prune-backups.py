#! /usr/local/bin/python3
import traceback
from loganne import updateLoganne
from schedule_tracker import updateScheduleTracker
from classes.host import Host

print ("\033[0mPruning Backups...", flush=True)
pruneCount = 0
failures = []

for host in Host.getAll():
	print("Host: {}".format(host.domain))
	for backup in host.getBackups():
		print("	Backup {} from {} - has {} instance(s)".format(backup.name, backup.source_hostname, len(backup.instances)))
		try:
			numberPruned = backup.prune(dryrun=False)
			if numberPruned > 0:
				print("		{} instances deleted".format(numberPruned))
			pruneCount += numberPruned
		except Exception as error:
			print("\033[91m** Error pruning backup {} ** {}\033[0m".format(backup.name, error), flush=True)
			traceback.print_exception(error)
			failures.append("{}/{}".format(host.domain, backup.name))
	host.closeConnection()

if failures:
	summary = "Prune failed for: {}".format(", ".join(failures))
	print("\033[91m** {} **\033[0m".format(summary), flush=True)
	updateScheduleTracker(system="lucos_backups_prune", success=False, message=summary)
else:
	print("\033[92mPruning Complete - {} backups pruned\033[0m".format(pruneCount), flush=True)
	if pruneCount > 0:
		updateLoganne(
			type="prune-backups",
			humanReadable="{} backups pruned".format(pruneCount),
		)
	updateScheduleTracker(system="lucos_backups_prune", success=True)
