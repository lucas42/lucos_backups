import os, sys, io
import fabric, paramiko

def getConnection(host):
	return fabric.Connection(
		host=host,
		user="lucos-backups",
		forward_agent=True,
	)