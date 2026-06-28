"""Authentication for lucos_backups using aithne local JWKS/JWT verification.

Implements the three-branch pattern from the estate consumer migration guide
(docs/consumer-migration-guide.md in lucas42/lucos_aithne, C1-C5):

  1. Valid aithne_session token AND backups:use scope → proceed.
  2. Valid token, missing backups:use → ForbiddenException (styled 403).
  3. No token or invalid/expired token → AuthException (303 → aithne login).
"""

import logging
import os
import re
import threading
import urllib.parse

import jwt
from jwt import PyJWKClient, PyJWKClientError

logger = logging.getLogger(__name__)

# PyJWKClientNetworkError was introduced in PyJWT 2.4.0 as a subclass of
# PyJWKClientError.  Fall back to the base class so the except clauses still
# catch network failures on older versions.
try:
	from jwt import PyJWKClientNetworkError
except ImportError:
	PyJWKClientNetworkError = PyJWKClientError

# AITHNE_ORIGIN is the browser-facing origin — used for the iss check AND the
# login-redirect base.  AITHNE_JWKS_URL overrides the server-side JWKS fetch
# address (needed in dev where the container cannot reach localhost:8039).
_AITHNE_ORIGIN = os.environ.get("AITHNE_ORIGIN", "https://aithne.l42.eu")
_AITHNE_JWKS_URL = (
	os.environ.get("AITHNE_JWKS_URL")
	or f"{_AITHNE_ORIGIN}/.well-known/jwks.json"
)
_AITHNE_ISSUER = _AITHNE_ORIGIN
_AITHNE_AUDIENCE = "l42.eu"
_REQUIRED_SCOPE = "backups:use"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AuthException(Exception):
	"""No valid session token — caller should redirect to aithne login."""
	pass


class ForbiddenException(Exception):
	"""Valid token but missing the required scope."""

	def __init__(self, required_scope):
		self.required_scope = required_scope
		super().__init__(f"Missing required scope: {required_scope}")


# ---------------------------------------------------------------------------
# JWKS client — last-known-good (LKG) wrapper
# ---------------------------------------------------------------------------

class _LKGJWKSClient:
	"""PyJWKClient wrapper that serves last-known-good keys on network failure.

	When the JWKS endpoint is transiently unreachable, falls back to the most
	recently fetched signing key rather than rejecting every token.  A cold
	start with no cached key fails closed (raises the original error).

	Per local-verification-contract.md §1 ("Serve last-known-good on a failed
	refresh") and lucas42/lucos_arachne#641.
	"""

	def __init__(self, uri):
		self._client = PyJWKClient(uri, cache_keys=True, lifespan=300)
		self._last_good_key = None
		self._lock = threading.Lock()

	def get_signing_key_from_jwt(self, token):
		try:
			key = self._client.get_signing_key_from_jwt(token)
			with self._lock:
				self._last_good_key = key
			return key
		except PyJWKClientNetworkError as exc:
			with self._lock:
				fallback = self._last_good_key
			safe_msg = re.sub(r'[\x00-\x1f\x7f]', '', str(exc))
			if fallback is None:
				logger.warning(
					"JWKS fetch failed at cold start (no cached key — failing closed): %s",
					safe_msg,
				)
				raise
			logger.warning("JWKS fetch failed (using last-known-good): %s", safe_msg)
			return fallback
		# Any other PyJWKClientError (e.g. kid not found after refresh) propagates.


# Module-level client shared across all requests.
_jwks_client = _LKGJWKSClient(_AITHNE_JWKS_URL)


def _set_jwks_client(client):
	"""Override the module-level JWKS client.  For testing only."""
	global _jwks_client
	_jwks_client = client


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def _verify_aithne_token(token_str):
	"""Verify an aithne-issued JWT.

	Returns (principal_class, sub, scopes) on success, or None on any failure.

	Validates: ES256 algorithm pinning, iss == AITHNE_ORIGIN, aud contains
	l42.eu, exp/iat with 30-second clock-skew leeway, required claims present,
	principal_class is a recognised value.
	"""
	# Phase 1 — resolve signing key from JWKS.
	try:
		signing_key = _jwks_client.get_signing_key_from_jwt(token_str)
	except PyJWKClientNetworkError:
		# Already logged by _LKGJWKSClient at WARNING.
		return None
	except PyJWKClientError as exc:
		safe_msg = re.sub(r'[\x00-\x1f\x7f]', '', str(exc))
		logger.warning("JWT rejected: JWKS client error (%s: %s)", type(exc).__name__, safe_msg)
		return None
	except jwt.DecodeError as exc:
		logger.warning("JWT rejected: malformed token (can't parse header) — %s", exc)
		return None

	# Phase 2 — decode and validate the JWT payload.
	try:
		payload = jwt.decode(
			token_str,
			signing_key.key,
			algorithms=["ES256"],
			issuer=_AITHNE_ISSUER,
			audience=_AITHNE_AUDIENCE,
			leeway=30,
			options={"require": ["exp", "iat", "sub"]},
		)
	except jwt.ExpiredSignatureError:
		logger.warning("JWT rejected: token has expired")
		return None
	except jwt.InvalidIssuerError:
		logger.warning("JWT rejected: wrong issuer (expected '%s')", _AITHNE_ISSUER)
		return None
	except jwt.InvalidAudienceError:
		logger.warning("JWT rejected: wrong audience (expected '%s')", _AITHNE_AUDIENCE)
		return None
	except jwt.MissingRequiredClaimError as exc:
		logger.warning("JWT rejected: missing required claim — %s", exc)
		return None
	except jwt.DecodeError as exc:
		logger.warning("JWT rejected: decode error — %s", exc)
		return None
	except jwt.InvalidTokenError as exc:
		logger.warning("JWT rejected: %s — %s", type(exc).__name__, exc)
		return None

	# Phase 3 — check principal_class is recognised (§5).
	principal_class = payload.get("principal_class")
	if principal_class not in ("human", "agent"):
		logger.warning("JWT rejected: unknown principal_class '%s'", principal_class)
		return None

	scopes = payload.get("scopes") or []
	sub = payload["sub"]
	logger.debug(
		"JWT verified: principal_class=%s sub=%.30s scopes=%s",
		principal_class, sub, scopes,
	)
	return (principal_class, sub, scopes)


# ---------------------------------------------------------------------------
# Auth helpers — called from server.py controllers
# ---------------------------------------------------------------------------

def checkAuth(handler):
	"""Enforce authentication and backups:use authorisation.

	Raises AuthException if no valid session is present.
	Raises ForbiddenException if authenticated but backups:use is not granted.
	Returns (principal_class, sub, scopes) on success.

	Scope check applies identically to human and agent principals per
	ADR-0001 §6 (access is granted by named scope, not principal_class).

	Development bypass: the estate-wide 'render-ui' scope is accepted as a
	pass when ENVIRONMENT == 'development' so lucos-ux can snapshot pages
	without provisioning a full aithne session per service.
	"""
	token = handler.cookies.get('aithne_session')
	if not token:
		raise AuthException("No aithne_session cookie")

	result = _verify_aithne_token(token)
	if result is None:
		raise AuthException("Invalid or expired session token")

	principal_class, sub, scopes = result

	# Development render-ui bypass (before the scope check).
	if (
		os.environ.get("ENVIRONMENT", "production") == "development"
		and "render-ui" in scopes
	):
		return (principal_class, sub, scopes)

	if _REQUIRED_SCOPE not in scopes:
		raise ForbiddenException(_REQUIRED_SCOPE)

	return (principal_class, sub, scopes)


def authenticate(handler):
	"""Send a 303 redirect to the aithne login page.

	Uses APP_ORIGIN (env var, provided by lucos_creds for every service) as the
	redirect base — no attacker-controlled data in the taint path, no dependency
	on proxy header forwarding behaviour.
	"""
	app_origin = os.environ.get("APP_ORIGIN", "")
	path = handler.parsed.path
	query = handler.parsed.query
	full_path = f"{path}?{query}" if query else path
	next_url = f"{app_origin}{full_path}"
	aithne_origin = os.environ.get("AITHNE_ORIGIN", "https://aithne.l42.eu")
	login_url = f"{aithne_origin}/auth/login?" + urllib.parse.urlencode({'next': next_url})
	handler.send_response(303)
	handler.send_header("Location", login_url)
	handler.end_headers()
