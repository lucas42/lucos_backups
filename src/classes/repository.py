'''
Repository
A source code repository
'''
import os, sys
import requests

if not os.environ.get("GITHUB_KEY"):
	sys.exit("\033[91mGITHUB_KEY not set\033[0m")
GITHUB_KEY=os.environ.get("GITHUB_KEY")

class Repository:

	def __init__(self, rawinfo):
		self.name = rawinfo['full_name']
		self.data = {
			'name': self.name,
			'size': rawinfo['size'],
			'url': rawinfo['html_url'],
			'archived': rawinfo['archived'],
			'fork': rawinfo['fork'],
		}

	def __str__(self):
		return "<One-Off File {} on {}>".format(self.name, self.host.name)

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