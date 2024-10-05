#! /usr/local/bin/python3
import json, sys, os, traceback, html
from http.server import BaseHTTPRequestHandler, HTTPServer
from tracking import fetchAllInfo

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
	def infoController(self):
		output = {
			"system": "lucos_backups",
			"title": "Backups",
			"ci": {
				"circle": "gh/lucas42/lucos_backups",
			},
			"checks": {
			},
			"metrics": {
			},
			"icon": "/icon.png",
			"network_only": True,
			"show_on_homepage": True,
		}
		self.send_response(200)
		self.send_header("Content-type", "application/json")
		self.end_headers()
		self.wfile.write(bytes(json.dumps(output, indent="\t")+"\n\n", "utf-8"))
	def summaryController(self):
		data = fetchAllInfo()
		dynamicContent = ""
		for host, info in data.items():
			dynamicContent += "<div class=\"host\"><h3>"+html.escape(host)+"</h3><h4>Backup Files</h4><table><thead><td>File Name</td><td>Modification Date</td></thead>"
			for file in info['backups']:
				dynamicContent += "<tr><td>"+html.escape(file['name'])+"</td><td>"+html.escape(file['date'])+"</td></tr>"
			if len(info['backups']) == 0:
				dynamicContent += "<tr><td class=\"error\" colspan=\"2\">No Files Found</td></tr>"
			dynamicContent += "</table>"
			dynamicContent += "<h4>Docker Volumes</h4><table><thead><td>Volume Name</td><td>Description</td><td>Rebuild Effort</td><td>Compose Project</td></thead>"
			for volume in info['volumes']:
				dynamicContent += "<tr><td>"+html.escape(volume['Name'])+"</td><td>"+html.escape(volume['description'])+"</td><td class=\"effort "+volume['effort']+"\">"+html.escape(volume['effort label'])+"</td><td>"+html.escape(volume['Labels']['com.docker.compose.project'])+"</td></tr>"
			if len(info['volumes']) == 0:
				dynamicContent += "<tr><td class=\"error\" colspan=\"2\">No Volumes Found</td></tr>"
			dynamicContent += "</table></div>"
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


if __name__ == "__main__":
	server = HTTPServer(('', port), BackupsHandler)
	print("Server started on port %s" % (port))
	server.serve_forever()
