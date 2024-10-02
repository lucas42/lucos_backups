import os, sys, io
import fabric, paramiko

if not os.environ.get("SSH_PRIVATE_KEY"):
	sys.exit("\033[91mSSH_PRIVATE_KEY not set\033[0m")

def getPrivateKey():
	rawString = os.environ.get("SSH_PRIVATE_KEY").replace("~","=") # Padding characters are stored as tildas due to limitation in lucos_creds
	fileObject = io.StringIO(rawString)
	return paramiko.ed25519key.Ed25519Key.from_private_key(fileObject)

def fetchInfoByHost(host):
	conn = fabric.Connection(
		host=host,
		user="lucos-backups",
		connect_kwargs={
			"pkey": getPrivateKey(),
		},
	)
	result = conn.run('ls -l --time-style=long-iso --literal /srv/backups', hide=True)
	raw_files = result.stdout.splitlines()
	del raw_files[0] # Drop the header line from ls
	files = []
	for file_info in raw_files:
		cols = file_info.split(maxsplit=7)
		files.append({
			"name": cols[7],
			"date": cols[5],
		})
	return files

def fetchAllInfo():
	return {
		"avalon": fetchInfoByHost("avalon.s.l42.eu"),
		"xwing": fetchInfoByHost("xwing.s.l42.eu"),
	}