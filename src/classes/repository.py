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
			# Skip storage-only hosts (e.g. aurora). They cannot fetch directly from
			# GitHub via wget — aurora's bundled OpenSSL is too old to negotiate the
			# TLS versions GitHub requires, so the wget below would fail and break
			# the loop for all subsequent hosts.
			#
			# CONFLATION CAVEAT (#229 hot-fix, see #228 for the proper fix):
			# is_storage_only semantically means "this host has no docker volumes /
			# one-off files of its own to back up" — used by getVolumes() and
			# getOneOffFiles() to short-circuit *source*-side iteration. Reusing it
			# here as a proxy for "this host can't reach external HTTPS endpoints"
			# is a related-but-not-identical concern that *coincides* on aurora
			# (currently the only host with both characteristics). A future
			# storage-only host with modern TLS would be unnecessarily skipped, and
			# a future non-storage-only host with broken TLS would still hit the
			# original failure mode. #228 tracks introducing a dedicated flag
			# (e.g. can_reach_external_services) so the two concerns are properly
			# separated.
			if host.is_storage_only:
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