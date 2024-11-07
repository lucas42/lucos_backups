'''
Repository
A source code repository
'''
import os, sys
import requests
from classes.host import Host
from datetime import datetime

ROOT_DIR = '/srv/backups/'

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
		return "<One-Off File {} on {}>".format(self.name, self.host.name)

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
		directory = "{ROOT_DIR}external/github/repository".format(ROOT_DIR=ROOT_DIR)
		date = datetime.today().strftime('%Y-%m-%d')
		archivePath = "{directory}/{repo_name}.{date}.tar.gz".format(directory=directory, repo_name=self.name, date=date)
		for host in Host.getAll():
			print("Archiving repo {name} to {host} at {archivePath}".format(name=self.name, host=host.name, archivePath=archivePath), flush=True)
			host.connection.run("mkdir -p {directory}".format(directory=directory), hide=True)
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