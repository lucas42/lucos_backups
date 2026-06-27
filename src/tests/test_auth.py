"""
Tests for utils/auth.py — aithne JWKS/JWT authentication and CSRF checks.

Tests the three-branch auth pattern (C2 from the migration guide):
  1. Valid token + backups:use → checkAuth returns successfully.
  2. Valid token, missing backups:use → ForbiddenException.
  3. No token or invalid token → AuthException.

CSRF (C5): checkCSRF validates Origin/Referer against APP_ORIGIN / *.l42.eu.
"""
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import utils.auth — it lives one level up from tests/
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import utils.auth as auth_module
from utils.auth import (
	AuthException,
	ForbiddenException,
	CSRFException,
	checkAuth,
	checkCSRF,
	authenticate,
	_set_jwks_client,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_handler(cookies=None, headers=None, path='/', query=''):
	"""Create a minimal mock handler for auth tests."""
	handler = MagicMock()
	handler.cookies = cookies or {}
	header_dict = headers or {}
	handler.headers = MagicMock()
	handler.headers.get = lambda key, default='': header_dict.get(key, default)
	handler.parsed = MagicMock()
	handler.parsed.path = path
	handler.parsed.query = query
	handler._response_code = None
	handler._response_location = None

	def _send_response(code):
		handler._response_code = code

	def _send_header(key, val):
		if key == 'Location':
			handler._response_location = val

	handler.send_response = _send_response
	handler.send_header = _send_header
	handler.end_headers = MagicMock()
	return handler


def _mock_signing_key():
	"""Return a MagicMock signing key that jwt.decode can use (with patching)."""
	key = MagicMock()
	key.key = 'fake-ec-key'
	return key


class _MockJWKSClient:
	"""Fake JWKS client that always returns a preset signing key."""

	def __init__(self, signing_key):
		self._signing_key = signing_key

	def get_signing_key_from_jwt(self, token):
		return self._signing_key


# ---------------------------------------------------------------------------
# checkAuth — three-branch tests
# ---------------------------------------------------------------------------

class TestCheckAuth:

	def setup_method(self):
		"""Inject a mock JWKS client before each test."""
		self._orig_client = auth_module._jwks_client
		self._mock_key = _mock_signing_key()
		_set_jwks_client(_MockJWKSClient(self._mock_key))

	def teardown_method(self):
		_set_jwks_client(self._orig_client)

	def _patch_decode(self, payload):
		"""Patch jwt.decode to return payload without hitting real crypto."""
		return patch('utils.auth.jwt.decode', return_value=payload)

	# Branch 1: valid token + required scope → success
	def test_valid_token_with_scope_succeeds(self):
		handler = _make_handler(cookies={'aithne_session': 'valid.jwt.token'})
		payload = {
			'principal_class': 'human',
			'sub': 'contact-abc',
			'scopes': ['backups:use'],
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			result = checkAuth(handler)
		assert result == ('human', 'contact-abc', ['backups:use'])

	def test_agent_with_scope_succeeds(self):
		"""Agents with backups:use are accepted — scope is the gate, not principal_class."""
		handler = _make_handler(cookies={'aithne_session': 'agent.jwt.token'})
		payload = {
			'principal_class': 'agent',
			'sub': 'lucos-developer',
			'scopes': ['backups:use', 'other:scope'],
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			result = checkAuth(handler)
		assert result[0] == 'agent'

	# Branch 2: valid token, missing scope → ForbiddenException
	def test_valid_token_missing_scope_raises_forbidden(self):
		handler = _make_handler(cookies={'aithne_session': 'valid.jwt.no-scope'})
		payload = {
			'principal_class': 'human',
			'sub': 'contact-abc',
			'scopes': ['some:other'],
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			with pytest.raises(ForbiddenException) as exc_info:
				checkAuth(handler)
		assert exc_info.value.required_scope == 'backups:use'

	def test_valid_token_empty_scopes_raises_forbidden(self):
		handler = _make_handler(cookies={'aithne_session': 'valid.jwt.empty'})
		payload = {
			'principal_class': 'human',
			'sub': 'contact-abc',
			'scopes': [],
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			with pytest.raises(ForbiddenException):
				checkAuth(handler)

	def test_forbidden_does_not_redirect(self):
		"""Branch 2 must raise ForbiddenException, not AuthException (no redirect loop)."""
		handler = _make_handler(cookies={'aithne_session': 'valid.jwt.no-scope'})
		payload = {
			'principal_class': 'human',
			'sub': 'contact-abc',
			'scopes': [],
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			with pytest.raises(ForbiddenException):
				checkAuth(handler)
		# AuthException (redirect) must NOT be raised
		assert handler._response_code is None

	# Branch 3: no/invalid token → AuthException
	def test_no_cookie_raises_auth_exception(self):
		handler = _make_handler(cookies={})
		with pytest.raises(AuthException):
			checkAuth(handler)

	def test_expired_token_raises_auth_exception(self):
		import jwt as pyjwt
		handler = _make_handler(cookies={'aithne_session': 'expired.jwt.token'})
		with patch('utils.auth.jwt.decode', side_effect=pyjwt.ExpiredSignatureError("expired")):
			with pytest.raises(AuthException):
				checkAuth(handler)

	def test_invalid_token_raises_auth_exception(self):
		import jwt as pyjwt
		handler = _make_handler(cookies={'aithne_session': 'bad.jwt.token'})
		with patch('utils.auth.jwt.decode', side_effect=pyjwt.DecodeError("bad")):
			with pytest.raises(AuthException):
				checkAuth(handler)

	def test_unknown_principal_class_raises_auth_exception(self):
		handler = _make_handler(cookies={'aithne_session': 'valid.jwt.unknown'})
		payload = {
			'principal_class': 'robot',  # not human or agent
			'sub': 'thing',
			'scopes': ['backups:use'],
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			with pytest.raises(AuthException):
				checkAuth(handler)

	# Development render-ui bypass
	def test_render_ui_bypass_in_development(self):
		handler = _make_handler(cookies={'aithne_session': 'dev.jwt.render-ui'})
		payload = {
			'principal_class': 'human',
			'sub': 'contact-abc',
			'scopes': ['render-ui'],  # no backups:use — dev bypass
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			with patch.dict('os.environ', {'ENVIRONMENT': 'development'}):
				result = checkAuth(handler)
		assert result[0] == 'human'

	def test_render_ui_bypass_not_active_in_production(self):
		handler = _make_handler(cookies={'aithne_session': 'prod.jwt.render-ui'})
		payload = {
			'principal_class': 'human',
			'sub': 'contact-abc',
			'scopes': ['render-ui'],  # production — bypass must not fire
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			with patch.dict('os.environ', {'ENVIRONMENT': 'production'}):
				with pytest.raises(ForbiddenException):
					checkAuth(handler)

	def test_render_ui_bypass_not_active_by_default(self):
		"""ENVIRONMENT defaults to production if unset — bypass must NOT fire."""
		handler = _make_handler(cookies={'aithne_session': 'no-env.jwt.render-ui'})
		payload = {
			'principal_class': 'human',
			'sub': 'contact-abc',
			'scopes': ['render-ui'],
			'exp': int(time.time()) + 900,
			'iat': int(time.time()),
		}
		with self._patch_decode(payload):
			env = {k: v for k, v in os.environ.items() if k != 'ENVIRONMENT'}
			with patch.dict('os.environ', env, clear=True):
				with pytest.raises(ForbiddenException):
					checkAuth(handler)


# ---------------------------------------------------------------------------
# checkCSRF
# ---------------------------------------------------------------------------

class TestCheckCSRF:

	def test_origin_matches_app_origin_passes(self):
		handler = _make_handler(headers={'Origin': 'https://backups.l42.eu'})
		with patch.dict('os.environ', {'APP_ORIGIN': 'https://backups.l42.eu'}):
			checkCSRF(handler)  # must not raise

	def test_origin_matches_l42eu_subdomain_passes(self):
		handler = _make_handler(headers={'Origin': 'https://aithne.l42.eu'})
		with patch.dict('os.environ', {'APP_ORIGIN': 'https://backups.l42.eu'}):
			checkCSRF(handler)

	def test_bare_l42eu_passes(self):
		handler = _make_handler(headers={'Origin': 'https://l42.eu'})
		checkCSRF(handler)

	def test_external_origin_raises_csrf_exception(self):
		handler = _make_handler(headers={'Origin': 'https://attacker.com'})
		with patch.dict('os.environ', {'APP_ORIGIN': 'https://backups.l42.eu'}):
			with pytest.raises(CSRFException):
				checkCSRF(handler)

	def test_missing_origin_and_referer_raises_csrf_exception(self):
		handler = _make_handler(headers={})
		with pytest.raises(CSRFException):
			checkCSRF(handler)

	def test_referer_fallback_from_own_origin_passes(self):
		"""When Origin is absent, Referer from APP_ORIGIN should pass."""
		handler = _make_handler(headers={'Referer': 'https://backups.l42.eu/some/page'})
		with patch.dict('os.environ', {'APP_ORIGIN': 'https://backups.l42.eu'}):
			checkCSRF(handler)

	def test_referer_fallback_from_external_raises(self):
		handler = _make_handler(headers={'Referer': 'https://evil.example.com/'})
		with patch.dict('os.environ', {'APP_ORIGIN': 'https://backups.l42.eu'}):
			with pytest.raises(CSRFException):
				checkCSRF(handler)

	def test_origin_with_port_from_app_origin_passes(self):
		"""APP_ORIGIN in dev includes port — Origin header should match."""
		handler = _make_handler(headers={'Origin': 'http://localhost:8083'})
		with patch.dict('os.environ', {'APP_ORIGIN': 'http://localhost:8083'}):
			checkCSRF(handler)


# ---------------------------------------------------------------------------
# authenticate — redirect target
# ---------------------------------------------------------------------------

class TestAuthenticate:

	def test_redirects_to_aithne_login(self):
		handler = _make_handler(
			headers={
				'X-Forwarded-Proto': 'https',
				'Host': 'backups.l42.eu',
			},
			path='/',
		)
		with patch.dict('os.environ', {'AITHNE_ORIGIN': 'https://aithne.l42.eu'}):
			authenticate(handler)

		assert handler._response_code == 303
		assert 'aithne.l42.eu/auth/login' in handler._response_location

	def test_next_url_is_full_absolute_url(self):
		handler = _make_handler(
			headers={
				'X-Forwarded-Proto': 'https',
				'Host': 'backups.l42.eu',
			},
			path='/hosts/avalon',
		)
		with patch.dict('os.environ', {'AITHNE_ORIGIN': 'https://aithne.l42.eu'}):
			authenticate(handler)

		location = handler._response_location
		# next= must be URL-encoded and contain the full host
		assert 'backups.l42.eu' in location
		assert 'next=' in location

	def test_next_url_includes_query_string(self):
		handler = _make_handler(
			headers={
				'X-Forwarded-Proto': 'https',
				'Host': 'backups.l42.eu',
			},
			path='/hosts/avalon',
			query='debug=true',
		)
		with patch.dict('os.environ', {'AITHNE_ORIGIN': 'https://aithne.l42.eu'}):
			authenticate(handler)

		location = handler._response_location
		# The query string must be included in the next= value
		assert 'debug' in location

	def test_uses_aithne_origin_env_var(self):
		handler = _make_handler(
			headers={'X-Forwarded-Proto': 'https', 'Host': 'backups.l42.eu'},
			path='/',
		)
		with patch.dict('os.environ', {'AITHNE_ORIGIN': 'http://localhost:8039'}):
			authenticate(handler)

		assert handler._response_location.startswith('http://localhost:8039/auth/login')
