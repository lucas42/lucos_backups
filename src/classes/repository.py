'''
Repository
A source code repository
'''
import os, sys
import requests
from classes.host import Host
from datetime import datetime

if not os.environ.get("GITHUB_KEY"):
	sys.exit("\033[91mGITHUB_KEY not set\033[0m")
GITHUB_KEY=os.environ.get("GITHUB_KEY")

class Repository:

	def __init__(self, rawinfo):
		self.name = rawinfo['name']
		self.data = {
			'name': self.name,
			'size': rawinfo['size'],
			'url': rawinfo['html_url'],
			'archived': rawinfo['archived'],
			'fork': rawinfo['fork'],
		}
		self.source_url = rawinfo['url']+"/tarball"

	def __str__(self):
		return "<Repository {}>".format(self.name)

	'''
	Returns the URL to download the repository from as a tarball
	For private repositories, this URL should be vaild for the next 5 minutes
	'''
	def getAuthenticatedDownloadUrl(self):
		resp = requests.get(
			url = self.source_url,
			headers = { "Authorization": "Bearer "+GITHUB_KEY},
			allow_redirects = False,
		)
		resp.raise_for_status()
		return resp.headers['Location']

	def backup(self):
		downloadUrl = self.getAuthenticatedDownloadUrl()
		date = datetime.today().strftime('%Y-%m-%d')
		for host in Host.getAll():
			# Skip hosts that cannot reach external HTTPS endpoints (e.g. aurora,
			# whose bundled OpenSSL is too old to negotiate the TLS versions that
			# GitHub codeload requires). The dedicated can_reach_external_services
			# flag is the correct gate here — is_storage_only is a distinct concern
			# (whether the host has its own docker volumes to back up) and must not
			# be conflated with external-network reachability (#228).
			if not host.can_reach_external_services:
				continue
			directory = "{backup_root}external/github/repository".format(backup_root=host.backup_root)
			archivePath = "{directory}/{repo_name}.{date}.tar.gz".format(directory=directory, repo_name=self.name, date=date)
			print("Archiving repo {name} to {host} at {archivePath}".format(name=self.name, host=host.name, archivePath=archivePath), flush=True)
			host.connection.run("mkdir -p {directory}".format(directory=directory), hide=True, timeout=3)
			host.connection.run("wget \"{url}\" -O \"{archivePath}\"".format(url=downloadUrl, archivePath=archivePath), hide=True)
			host.closeConnection()
		return 1

	def getData(self):
		return self.data

	@classmethod
	def getAll(cls):
		resp = requests.get("https://api.github.com/user/repos?affiliation=owner&per_page=100", headers={
			"Authorization": "Bearer "+GITHUB_KEY
		})
		resp.raise_for_status()
		repositories = [Repository(rawinfo) for rawinfo in resp.json()]
		if (len(repositories) >= 100):
			print("\033[91m** Error ** Maximum repositories available in one request.  Need pagination to retreive more\033[0m", flush=True)
		return repositories