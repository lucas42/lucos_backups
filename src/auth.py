import requests, urllib
valid_tokens = [] # A cache of tokens which are known to be valid

def isAuthenticated(token):
	if not token:
		return False
	if token in valid_tokens:
		return True
	try:
		response = requests.get('https://auth.l42.eu/data?'+urllib.parse.urlencode({'token': token}))
		response.raise_for_status() # Invalid tokens will return a 401 response
		valid_tokens.append(token)
		return True
	except Exception as error:
		print ("\033[91m** Authentication Error ** " + str(error) + "\033[0m")
		return False

def authenticate(handler):
	redirect_url = "{}://{}{}".format(handler.headers.get('X-Forwarded-Proto', 'http'), handler.headers.get('Host'), handler.parsed.path)
	handler.send_response(303)
	handler.send_header("Location", "https://auth.l42.eu/authenticate?"+urllib.parse.urlencode({'redirect_uri': redirect_url}))
	handler.end_headers()