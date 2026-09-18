"""
Tests for scripts/quiesce-capture.sh — the host-side bracket that pauses a
volume's writers around its local read (#344).

The one property that matters most is that a writer is never left paused, so
these run the real script against a fake `docker` on PATH which records pause
state as files.  No docker daemon is needed.
"""
import os
import signal
import stat
import subprocess
import time
import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "quiesce-capture.sh")

FAKE_DOCKER = r'''#!/bin/sh
state="$FAKE_DOCKER_STATE"
echo "$*" >> "$state/calls"
case "$1" in
	image|pull) exit 0 ;;
	pause)
		shift; rc=0
		for c in "$@"; do
			if [ "$c" = vanished ]; then echo "No such container: $c" >&2; rc=1; continue; fi
			touch "$state/paused-$c"
		done
		exit $rc ;;
	unpause)
		shift
		[ -n "${FAKE_UNPAUSE_FAILS:-}" ] && exit 1
		for c in "$@"; do rm -f "$state/paused-$c"; done
		exit 0 ;;
	inspect)
		for c; do :; done
		[ "$c" = vanished ] && exit 1
		if [ -e "$state/paused-$c" ]; then echo true; else echo false; fi
		exit 0 ;;
	run)
		touch "$state/capture-started"
		# exec, so a signal reaches the "capture" itself rather than orphaning it
		[ -n "${FAKE_CAPTURE_SECONDS:-}" ] && exec sleep "$FAKE_CAPTURE_SECONDS"
		exit "${FAKE_CAPTURE_EXIT:-0}" ;;
esac
'''


@pytest.fixture
def fake_docker(tmp_path):
	bin_dir = tmp_path / "bin"
	state = tmp_path / "state"
	bin_dir.mkdir()
	state.mkdir()
	docker = bin_dir / "docker"
	docker.write_text(FAKE_DOCKER)
	docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
	env = dict(os.environ)
	env["PATH"] = "{}:{}".format(bin_dir, env["PATH"])
	env["FAKE_DOCKER_STATE"] = str(state)
	return env, state


def args(writers, capture_timeout="10", watchdog="2"):
	return ["sh", SCRIPT, capture_timeout, watchdog, "vol", "/srv/backups/local", "/srv/backups/local/.staging/vol.tar"] + writers


def paused(state):
	return sorted(p.name[len("paused-"):] for p in state.iterdir() if p.name.startswith("paused-"))


def calls(state):
	path = state / "calls"
	return path.read_text().splitlines() if path.exists() else []


def wait_for(predicate, timeout=5):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if predicate():
			return True
		time.sleep(0.02)
	return False


def test_success_pauses_captures_and_unpauses(fake_docker):
	env, state = fake_docker
	result = subprocess.run(args(["writer_a", "writer_b"]), env=env, capture_output=True, text=True, timeout=20)
	assert result.returncode == 0, result.stderr
	assert paused(state) == []
	verbs = [c.split()[0] for c in calls(state) if c.split()[0] in ("pause", "run", "unpause")]
	assert verbs == ["pause", "run", "unpause"]
	assert "Paused for" in result.stdout


def test_no_writers_captures_without_pausing(fake_docker):
	env, state = fake_docker
	result = subprocess.run(args([]), env=env, capture_output=True, text=True, timeout=20)
	assert result.returncode == 0, result.stderr
	assert not any(c.startswith("pause") for c in calls(state))
	assert any(c.startswith("run") for c in calls(state))


def test_capture_failure_still_unpauses_and_fails(fake_docker):
	env, state = fake_docker
	env["FAKE_CAPTURE_EXIT"] = "3"
	result = subprocess.run(args(["writer_a"]), env=env, capture_output=True, text=True, timeout=20)
	assert result.returncode == 3
	assert paused(state) == []


def test_capture_over_time_bound_is_stopped_and_unpaused(fake_docker):
	env, state = fake_docker
	env["FAKE_CAPTURE_SECONDS"] = "15"
	started = time.monotonic()
	result = subprocess.run(args(["writer_a"], capture_timeout="1"), env=env, capture_output=True, text=True, timeout=20)
	assert result.returncode != 0
	assert paused(state) == []
	assert time.monotonic() - started < 10


def test_sigterm_mid_capture_unpauses_promptly(fake_docker):
	env, state = fake_docker
	env["FAKE_CAPTURE_SECONDS"] = "15"
	proc = subprocess.Popen(args(["writer_a"]), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	assert wait_for(lambda: (state / "capture-started").exists())
	assert paused(state) == ["writer_a"]
	proc.send_signal(signal.SIGTERM)
	proc.wait(timeout=10)
	assert proc.returncode != 0
	assert paused(state) == []


def test_watchdog_unpauses_when_script_is_killed(fake_docker):
	env, state = fake_docker
	env["FAKE_CAPTURE_SECONDS"] = "15"
	proc = subprocess.Popen(args(["writer_a"], watchdog="1"), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	assert wait_for(lambda: (state / "capture-started").exists())
	proc.kill()
	proc.wait(timeout=5)
	assert paused(state) == ["writer_a"]
	assert wait_for(lambda: paused(state) == [], timeout=10)


def test_writer_that_vanished_fails_loudly_but_others_are_unpaused(fake_docker):
	env, state = fake_docker
	result = subprocess.run(args(["writer_a", "vanished"]), env=env, capture_output=True, text=True, timeout=20)
	# Fails on the pause itself — a container that's gone is not "still paused".
	assert result.returncode == 1
	assert "Still paused" not in result.stderr
	assert paused(state) == []


def test_writer_still_paused_after_release_fails_with_distinct_status(fake_docker):
	env, state = fake_docker
	env["FAKE_UNPAUSE_FAILS"] = "1"
	result = subprocess.run(args(["writer_a"], watchdog="1"), env=env, capture_output=True, text=True, timeout=20)
	assert result.returncode == 70
	assert "Still paused" in result.stderr
