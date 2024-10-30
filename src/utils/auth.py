import requests, urllib
valid_tokens = [] # A cache of tokens which are known to be valid

class AuthException(Exception):
	pass

def checkAuth(handler):
	token = handler.parsed_query.get('token') or handler.cookies.get('token')
	if not token:
		raise AuthException("No token found")
	if token in valid_tokens:
		return True
	try:
		response = requests.get('https://auth.l42.eu/data?'+urllib.parse.urlencode({'token': token}))
		response.raise_for_status() # Invalid tokens will return a 401 response
		valid_tokens.append(token)
		return True
	except Exception as error:
		print ("\033[91m** Authentication Error ** " + str(error) + "\033[0m", flush=True)
		raise AuthException(str(error))

def authenticate(handler):
	redirect_url = "{}://{}{}".format(handler.headers.get('X-Forwarded-Proto', 'http'), handler.headers.get('Host'), handler.parsed.path)
	handler.send_response(303)
	handler.send_header("Location", "https://auth.l42.eu/authenticate?"+urllib.parse.urlencode({'redirect_uri': redirect_url}))
	handler.end_headers()

def setAuthCookies(handler):
	if handler.parsed_query.get('token') is not None and handler.cookies.get('token') != handler.parsed_query.get('token'):
		handler.send_header("Set-Cookie", "token="+handler.parsed_query.get('token'))