#! /usr/local/bin/python3
import fcntl
import os
import sys
import time
import traceback
from loganne import updateLoganne
from schedule_tracker import updateScheduleTracker
from classes.host import Host
from classes.repository import Repository

LOCK_FILE = '/var/run/lucos_backups/create.lock'
LAST_SUCCESS_FILE = '/var/run/lucos_backups/last_success'
FRESH_THRESHOLD_SECONDS = 20 * 60 * 60  # 20 hours — comfortably less than 24h to handle cron drift


def run(lock_file=LOCK_FILE, last_success_file=LAST_SUCCESS_FILE, fresh_threshold_seconds=FRESH_THRESHOLD_SECONDS):
	"""Run the backup script with skip-if-fresh and concurrency protection.

	Parameterised for testability — callers can supply alternative paths for
	the lockfile and last_success marker so tests don't need /var/run/.
	"""

	# Ensure the runtime directory exists
	os.makedirs(os.path.dirname(lock_file), exist_ok=True)

	# --- Acquire lock (guard against concurrent runs) ---
	# If the 03:25 run is still running at 15:25, the 15:25 run exits cleanly
	# with a success tick.  The in-flight run will write the last_success marker
	# and emit its own tracker update when it completes.
	_lockfile = open(lock_file, 'w')
	try:
		fcntl.flock(_lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
	except BlockingIOError:
		print("No-op: previous run still in flight", flush=True)
		updateScheduleTracker(success=True, message="No-op: previous run still in flight")
		sys.exit(0)

	# --- Skip-if-fresh check ---
	# If the last successful run completed within the threshold, nothing to do.
	# The no-op still emits a success tick so schedule-tracker stays green.
	if os.path.exists(last_success_file):
		age_seconds = time.time() - os.path.getmtime(last_success_file)
		if age_seconds < fresh_threshold_seconds:
			print("No-op: recent successful backup ({:.0f}h ago), skipping".format(age_seconds / 3600), flush=True)
			updateScheduleTracker(success=True, message="No-op: recent successful backup")
			sys.exit(0)

	print("\033[0mStarting Backups...", flush=True)

	backupCount = 0
	failures = []

	for host in Host.getAll():
		print("Host:", host.domain, flush=True)
		for volume in host.getVolumes():
			try:
				backupCount += volume.backup()
			except Exception as error:
				print("\033[91m** Error backing up volume {} ** {}\033[0m".format(volume.name, error), flush=True)
				traceback.print_exception(error)
				failures.append("volume:{}/{}".format(host.domain, volume.name))
		for file in host.getOneOffFiles():
			try:
				backupCount += file.backup()
			except Exception as error:
				print("\033[91m** Error backing up file {} ** {}\033[0m".format(file.name, error), flush=True)
				traceback.print_exception(error)
				failures.append("file:{}/{}".format(host.domain, file.name))
		host.closeConnection()

	for repo in Repository.getAll():
		try:
			backupCount += repo.backup()
		except Exception as error:
			print("\033[91m** Error backing up repo {} ** {}\033[0m".format(repo.name, error), flush=True)
			traceback.print_exception(error)
			failures.append("repo:{}".format(repo.name))

	if failures:
		summary = "Backups failed for: {}".format(", ".join(failures))
		print("\033[91m** {} **\033[0m".format(summary), flush=True)
		updateScheduleTracker(success=False, message=summary)
	else:
		print("\033[92m" + "Backups Complete" + "\033[0m", flush=True)
		if backupCount > 0:
			updateLoganne(
				type="backups",
				humanReadable="{} archives successfully backed up".format(backupCount),
			)
		# Write last_success marker so the next cron run can skip if this completed recently
		with open(last_success_file, 'w') as f:
			f.write('')
		updateScheduleTracker(success=True)


if __name__ == '__main__':
	run()
