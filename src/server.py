#! /usr/local/bin/python3
import json, sys, os, traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

if not os.environ.get("PORT"):
	sys.exit("\033[91mPORT not set\033[0m")
try:
	port = int(os.environ.get("PORT"))
except ValueError:
	sys.exit("\033[91mPORT isn't an integer\033[0m")

class BackupsHandler(BaseHTTPRequestHandler):
	def do_GET(self):
		if (self.path == "/_info"):
			self.infoController()
		else:
			self.send_error(404, "Page Not Found")
		self.wfile.flush()
		self.connection.close()
	def infoController(self):
		output = {
			"system": "lucos_backups",
			"ci": {
				"circle": "gh/lucas42/lucos_backups",
			},
			"checks": {
			},
			"metrics": {
			},
			"network_only": True,
			"show_on_homepage": False,
		}
		self.send_response(200)
		self.send_header("Content-type", "application/json")
		self.end_headers()
		self.wfile.write(bytes(json.dumps(output, indent="\t")+"\n\n", "utf-8"))

if __name__ == "__main__":
	server = HTTPServer(('', port), BackupsHandler)
	print("Server started on port %s" % (port))
	server.serve_forever()
