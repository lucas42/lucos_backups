#! /usr/local/bin/python3
'''
seed-volume

Off-cron seed of a single incremental-strategy volume (ADR-0002, C5).

The first ever snapshot of an incremental volume is a *full* transfer (there is
no previous snapshot to --link-dest against).  For a large volume (e.g. the
~85GB post-migration photos volume) that one-time copy must not run inside the
nightly create-backups cron, where it would starve every other volume's backup
of the shared home-network pipe.  This script runs that first snapshot on demand,
off the cron critical path; once it has completed, the nightly run finds the seed
snapshot and ships only deltas.

    *** AD-HOC USE ONLY ***
This is run manually by an admin (or as choreographed by a migration plan — see
lucas42/lucos_photos#427), not by the application, web server, or cron.

Usage (inside the lucos_backups container):
    pipenv run python -m scripts.seed-volume <volume_name>

It finds <volume_name> on whichever host it lives on and runs the same
incremental backup the nightly cron would, transferring to every destination
host the volume isn't skipped on.  Re-running is safe: it resumes a partial
transfer and republishes the snapshot atomically.
'''
import sys
import traceback
from classes.host import Host


def run(volume_name):
	print("\033[0mSeeding incremental volume {}...".format(volume_name), flush=True)

	target_volume = None
	for host in Host.getAll():
		try:
			for volume in host.getVolumes():
				if volume.name == volume_name:
					target_volume = volume
					break
		except Exception as error:
			print("\033[91m** Error connecting to host {} ** {}\033[0m".format(host.domain, error), flush=True)
		if target_volume:
			break
		host.closeConnection()

	if target_volume is None:
		print("\033[91m** Error ** Volume {} not found on any host\033[0m".format(volume_name), flush=True)
		sys.exit(1)

	if target_volume.backup_strategy != "incremental":
		print("\033[91m** Error ** Volume {} has backup_strategy '{}', not 'incremental' — nothing to seed\033[0m".format(
			volume_name, target_volume.backup_strategy), flush=True)
		target_volume.host.closeConnection()
		sys.exit(1)

	try:
		target_volume.backupIncremental()
		print("\033[92mSeed complete for {}\033[0m".format(volume_name), flush=True)
	except Exception as error:
		print("\033[91m** Error seeding {} ** {}\033[0m".format(volume_name, error), flush=True)
		traceback.print_exception(error)
		sys.exit(1)
	finally:
		target_volume.host.closeConnection()


if __name__ == '__main__':
	if len(sys.argv) != 2:
		print("Usage: python -m scripts.seed-volume <volume_name>", flush=True)
		sys.exit(2)
	run(sys.argv[1])
