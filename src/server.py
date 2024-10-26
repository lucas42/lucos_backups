#! /usr/local/bin/python3
import json, sys, os, traceback, html, datetime, zoneinfo, urllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import SimpleCookie
from tracking import getAllInfo, fetchAllInfo
from schedule_tracker import updateScheduleTracker
from jinja2 import Environment, FileSystemLoader, select_autoescape
from auth import checkAuth, authenticate, setAuthCookies, AuthException

if not os.environ.get("PORT"):
	sys.exit("\033[91mPORT not set\033[0m")
try:
	port = int(os.environ.get("PORT"))
except ValueError:
	sys.exit("\033[91mPORT isn't an integer\033[0m")

def toLondonTime(value):
	return value.astimezone(zoneinfo.ZoneInfo("Europe/London")).strftime('%H:%M %Y-%m-%d')

templateEnv = Environment(loader=FileSystemLoader("templates/"), autoescape=select_autoescape())
templateEnv.filters["london_time"] = toLondonTime

class BackupsHandler(BaseHTTPRequestHandler):
	def do_GET(self):
		self.method = "GET"
		self.frontController()
	def do_POST(self):
		self.method = "POST"
		self.frontController()
	def frontController(self):
		try:
			self.parsed = urllib.parse.urlparse(self.path)
			self.parsed_query = dict(urllib.parse.parse_qsl(self.parsed.query))
			cookies = SimpleCookie()
			cookies.load(self.headers.get('Cookie', ''))
			self.cookies = {k: v.value for k, v in cookies.items()}
			if (self.parsed.path == "/" or self.parsed.path == "/hosts" or self.parsed.path == "/hosts/"):
				self.summaryController()
			elif (self.parsed.path.startswith("/hosts/")):
				self.hostController()
			elif (self.parsed.path == "/lucos_navbar.js"):
				self.staticFileController("lucos_navbar.js", "text/javascript")
			elif (self.parsed.path == "/style.css"):
				self.staticFileController("style.css", "text/css")
			elif (self.parsed.path == "/icon.png"):
				self.staticFileController("icon.png", "image/png")
			elif (self.parsed.path == "/_info"):
				self.infoController()
			elif (self.parsed.path == "/refresh-tracking"):
				self.refreshTrackingController()
			else:
				self.send_error(404, "Page Not Found")
		except AuthException:
			authenticate(self)
		self.wfile.flush()
		self.connection.close()
	def infoController(self):
		data = getAllInfo()
		data_age = datetime.datetime.now(datetime.timezone.utc) - data["update_time"]
		output = {
			"system": "lucos_backups",
			"title": "Backups",
			"ci": {
				"circle": "gh/lucas42/lucos_backups",
			},
			"checks": {
				"volume-config": {
					"techDetail": "Whether any docker volumes found on hosts aren't in config.yaml",
					"ok": (len(data["notInConfig"]) == 0),
				},
				"volume-host": {
					"techDetail": "Whether any volumes in config.yaml aren't found on at least one host",
					"ok": (len(data["notOnHost"]) == 0),
				},
				"data-age": {
					"techDetail": "Whether the data being used to track backups is more than 2 hours old",
					"ok": (data_age < datetime.timedelta(hours=2)),
				}
			},
			"metrics": {
				"host-count": {
					"techDetail": "The number of hosts being tracked for backups",
					"value": len(data["hosts"]),
				},
				"volume-count": {
					"techDetail": "The number of docker volumes found across all hosts",
					"value": len(data["volumes"]),
				},
			},
			"icon": "/icon.png",
			"network_only": True,
			"show_on_homepage": True,
		}
		if not output["checks"]["volume-config"]["ok"]:
			output["checks"]["volume-config"]["debug"] = "Volumes missing from volumes.yaml: "+", ".join(data["notInConfig"])
		if not output["checks"]["volume-host"]["ok"]:
			output["checks"]["volume-host"]["debug"] = "Volumes not found on host: "+", ".join(data["notOnHost"])
		if not output["checks"]["data-age"]["ok"]:
			output["checks"]["data-age"]["debug"] = "Last updated: "+str(data["update_time"])
		self.send_response(200)
		self.send_header("Content-type", "application/json")
		self.end_headers()
		self.wfile.write(bytes(json.dumps(output, indent="\t")+"\n\n", "utf-8"))
	def summaryController(self):
		checkAuth(self)
		output = templateEnv.get_template("summary.html").render(getAllInfo())
		self.send_response(200)
		self.send_header("Content-type", "text/html")
		setAuthCookies(self)
		self.end_headers()
		self.wfile.write(bytes(output, "utf-8"))
	def hostController(self):
		checkAuth(self)
		hostname = self.parsed.path.replace("/hosts/", "")
		info = getAllInfo()
		if hostname not in info['hosts']:
			self.send_error(404, "Host {} Not Found".format(hostname))
			return
		output = templateEnv.get_template("host.html").render({
			'host': hostname,
			'info': info['hosts'][hostname],
			'update_time': info['update_time'],
		})
		self.send_response(200)
		self.send_header("Content-type", "text/html")
		setAuthCookies(self)
		self.end_headers()
		self.wfile.write(bytes(output, "utf-8"))
	def staticFileController(self, filename, contentType):
		template = open("resources/"+filename, 'rb')
		self.send_response(200)
		self.send_header("Content-type", contentType)
		self.end_headers()
		self.wfile.write(template.read())
		template.close()
	def refreshTrackingController(self):
		if self.method != "POST":
			self.send_response(405)
			self.send_header("Allow", "POST")
			self.end_headers()
			return
		print ("\033[0mTracking Backups...")
		try:
			fetchAllInfo()
			print("\033[92m" + "Tracking completed successfully" + "\033[0m")
			updateScheduleTracker(
				system="lucos_backups_tracking",
				success=True,
				frequency=60*60, # 1 hour in seconds
			)
			self.send_response(303)
			self.send_header("Location", "/")
			self.end_headers()
		except Exception as error:
			print ("\033[91m** Error ** " + str(error) + "\033[0m")
			updateScheduleTracker(
				system="lucos_backups_tracking",
				success=False,
				message=str(error),
				frequency=60*60, # 1 hour in seconds
			)
			self.send_response(500)
			self.send_header("Content-type", "text/plain")
			self.end_headers()
			self.wfile.write(bytes("Error refreshing tracking: "+str(error)+"\n\n", "utf-8"))




if __name__ == "__main__":
	server = HTTPServer(('', port), BackupsHandler)
	print("Server started on port %s" % (port))
	server.serve_forever()
