#! /usr/local/bin/python3
import traceback
from loganne import updateLoganne
from schedule_tracker import updateScheduleTracker
from classes.host import Host


def run():
	print("\033[0mPruning Backups...", flush=True)
	pruneCount = 0
	failures = []

	for host in Host.getAll():
		print("Host: {}".format(host.domain))
		try:
			backups = host.getBackups()
		except Exception as error:
			print("\033[91m** Error fetching backups for host {} ** {}\033[0m".format(host.domain, error), flush=True)
			traceback.print_exception(error)
			failures.append("{} (host unreachable)".format(host.domain))
			host.closeConnection()
			continue
		for backup in backups:
			print("\tBackup {} from {} - has {} instance(s)".format(backup.name, backup.source_hostname, len(backup.instances)))
			try:
				numberPruned = backup.prune(dryrun=False)
				if numberPruned > 0:
					print("\t\t{} instances deleted".format(numberPruned))
				pruneCount += numberPruned
			except Exception as error:
				print("\033[91m** Error pruning backup {} ** {}\033[0m".format(backup.name, error), flush=True)
				traceback.print_exception(error)
				failures.append("{}/{}".format(host.domain, backup.name))
		host.closeConnection()

	if failures:
		summary = "Prune failed for: {}".format(", ".join(failures))
		print("\033[91m** {} **\033[0m".format(summary), flush=True)
		updateScheduleTracker(success=False, job_name="prune-backups", message=summary)
	else:
		print("\033[92mPruning Complete - {} backups pruned\033[0m".format(pruneCount), flush=True)
		if pruneCount > 0:
			updateLoganne(
				type="prune-backups",
				humanReadable="{} backups pruned".format(pruneCount),
			)
		updateScheduleTracker(success=True, job_name="prune-backups")


if __name__ == '__main__':
	run()
