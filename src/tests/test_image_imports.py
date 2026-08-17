"""Import the real production module chain, with nothing stubbed.

Every other test in this suite replaces the external dependencies with
MagicMock, so none of them execute the third-party imports the container
performs at startup. On 2026-08-17 that let an image which died on
`import requests` pass CI while crash-looping in production for 15h24m
(lucas42/lucos_backups#390).

These tests are only meaningful when run *inside the built image* — see the
`test` stage in the Dockerfile and the CI job that builds it.
"""
import importlib
import os
import sys
from unittest.mock import MagicMock

import pytest


# Mirrors the environment declared in docker-compose.yml. Several modules call
# sys.exit() at import time when their variable is missing (server.py on PORT,
# and the loganne and schedule_tracker clients on their endpoints), so these
# have to be present before any real import is attempted.
PRODUCTION_ENV = {
	"SYSTEM": "lucos_backups",
	"ENVIRONMENT": "test",
	"APP_ORIGIN": "http://localhost:9999",
	"PORT": "9999",
	"VERSION": "0.0.0-test",
	"GITHUB_KEY": "not-a-real-key",
	"SSH_PRIVATE_KEY": "not-a-real-key",
	"SCHEDULE_TRACKER_ENDPOINT": "http://localhost:9998/v2/report-status",
	"LOGANNE_ENDPOINT": "http://localhost:9997/events",
	"AITHNE_ORIGIN": "http://localhost:9996",
	"AITHNE_JWKS_URL": "http://localhost:9996/.well-known/jwks.json",
}


@pytest.fixture(autouse=True)
def production_env():
	for key, value in PRODUCTION_ENV.items():
		os.environ.setdefault(key, value)


def test_server_imports_with_real_dependencies():
	"""The web entrypoint's own import chain, unstubbed.

	Only utils.tracking is replaced, because importing it spawns a background
	fetch thread (utils/tracking.py sets up _initial_thread at module level).
	Everything else — jinja2, utils.auth, utils.config and the requests stack
	underneath it — is imported for real, which is the path that failed.
	"""
	sys.modules.setdefault("utils.tracking", MagicMock())
	sys.modules.pop("server", None)
	importlib.import_module("server")


# The scheduled-job entrypoints (scripts/create-backups.py, prune-backups.py)
# reach fabric and invoke via classes.host, which the web entrypoint does not
# pull once utils.tracking is stubbed. Named individually because the script
# filenames are hyphenated and so cannot be imported directly.
CRON_PATH_MODULES = [
	"classes.host",
	"classes.repository",
	"classes.volume",
	"utils.config",
	"loganne",
	"schedule_tracker",
]


@pytest.mark.parametrize("module_name", CRON_PATH_MODULES)
def test_cron_path_module_imports(module_name):
	importlib.import_module(module_name)
