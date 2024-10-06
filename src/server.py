#! /usr/local/bin/python3
import json, sys, os, traceback, html, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from tracking import getAllInfo, fetchAllInfo
from schedule_tracker import updateScheduleTracker

if not os.environ.get("PORT"):
	sys.exit("\033[91mPORT not set\033[0m")
try:
	port = int(os.environ.get("PORT"))
except ValueError:
	sys.exit("\033[91mPORT isn't an integer\033[0m")

class BackupsHandler(BaseHTTPRequestHandler):
	def do_GET(self):
		if (self.path == "/"):
			self.summaryController()
		elif (self.path == "/lucos_navbar.js"):
			self.staticFileController("lucos_navbar.js", "text/javascript")
		elif (self.path == "/style.css"):
			self.staticFileController("style.css", "text/css")
		elif (self.path == "/icon.png"):
			self.staticFileController("icon.png", "image/png")
		elif (self.path == "/_info"):
			self.infoController()
		else:
			self.send_error(404, "Page Not Found")
		self.wfile.flush()
		self.connection.close()
	def do_POST(self):
		if (self.path == "/refresh-tracking"):
			self.refreshTrackingController()
		else:
			self.send_error(404, "Page Not Found")
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
		data = getAllInfo()
		dynamicContent = ""
		for host, info in data["hosts"].items():
			dynamicContent += "<div class=\"host\"><h3>"+html.escape(host)+"</h3><h4>Backup Files</h4><table><thead><td>File Name</td><td>Modification Date</td></thead>"
			for file in info['backups']:
				dynamicContent += "<tr><td>"+html.escape(file['name'])+"</td><td>"+html.escape(file['date'])+"</td></tr>"
			if len(info['backups']) == 0:
				dynamicContent += "<tr><td class=\"error\" colspan=\"2\">No Files Found</td></tr>"
			dynamicContent += "</table>"
			dynamicContent += "<h4>Docker Volumes</h4><table><thead><td>Volume Name</td><td>Description</td><td>Rebuild Effort</td><td>Project</td></thead>"
			for volume in info['volumes']:
				dynamicContent += "<tr><td>"+html.escape(volume['Name'])+"</td><td>"+html.escape(volume['description'])+"</td><td class=\"effort "+volume['effort']+"\">"+html.escape(volume['effort label'])+"</td><td><a href=\""+html.escape(volume['project link'])+"\" target=\"_blank\">"+html.escape(volume['Labels']['com.docker.compose.project'])+"</a></td></tr>"
			if len(info['volumes']) == 0:
				dynamicContent += "<tr><td class=\"error\" colspan=\"2\">No Volumes Found</td></tr>"
			dynamicContent += "</table></div>"
		dynamicContent += "<footer>Last updated <time datetime=\""+html.escape(str(data["update_time"]))+"\">"+html.escape(str(data["update_time"]))+"</time></footer>"
		template = open("resources/summary.html", 'r')
		output = template.read().replace("$$DATA$$", dynamicContent)
		self.send_response(200)
		self.send_header("Content-type", "text/html")
		self.end_headers()
		self.wfile.write(bytes(output, "utf-8"))
		template.close()
	def staticFileController(self, filename, contentType):
		template = open("resources/"+filename, 'rb')
		self.send_response(200)
		self.send_header("Content-type", contentType)
		self.end_headers()
		self.wfile.write(template.read())
		template.close()
	def refreshTrackingController(self):
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
