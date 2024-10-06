import os, sys, io
import fabric, paramiko

if not os.environ.get("SSH_PRIVATE_KEY"):
	sys.exit("\033[91mSSH_PRIVATE_KEY not set\033[0m")

def getPrivateKey():
	rawString = os.environ.get("SSH_PRIVATE_KEY").replace("~","=") # Padding characters are stored as tildas due to limitation in lucos_creds
	fileObject = io.StringIO(rawString)
	return paramiko.ed25519key.Ed25519Key.from_private_key(fileObject)

def getConnection(host):
	return fabric.Connection(
		host=host,
		user="lucos-backups",
		connect_kwargs={
			"pkey": getPrivateKey(),
		},
	)