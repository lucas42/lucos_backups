#! /usr/local/bin/python3
import traceback
from loganne import updateLoganne
from schedule_tracker import updateScheduleTracker
from classes.host import Host
from classes.repository import Repository

print ("\033[0mStarting Backups...", flush=True)

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
	updateScheduleTracker(success=True)
