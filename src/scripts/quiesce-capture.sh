#!/bin/sh
# Takes an uncompressed tar of a docker volume while every container writing to
# it is paused, so the capture is one point in time rather than a smear across
# live writes (lucas42/lucos_backups#344; ADR-0002, consistency).
#
# Runs on the SOURCE host (sent over the Fabric SSH connection by
# Volume.archiveLocally), not inside the lucos_backups container.
#
# Usage: quiesce-capture.sh <capture-timeout-s> <watchdog-s> <volume> <mount-dir> <staging-path> [writer...]
#
# Unpause is guaranteed three ways: the EXIT trap (success, error, timeout); HUP,
# INT and TERM are converted into an exit so the trap still runs if the SSH
# session drops; and a nohup'd watchdog unpauses after <watchdog-s> even if this
# script is SIGKILLed.  The exit status is non-zero on any failure, and on the
# exact failure that matters most — a writer still paused afterwards.
set -u

capture_timeout=$1
watchdog_seconds=$2
volume=$3
mount_dir=$4
staging_path=$5
shift 5
# "$@" is now the list of writer containers (possibly empty).

image=alpine:latest

# Never let an image pull happen inside the freeze.
docker image inspect "$image" >/dev/null 2>&1 || docker pull -q "$image" >/dev/null || exit 1

now_ms() {
	ms=$(date +%s%3N 2>/dev/null)
	case "$ms" in
		''|*[!0-9]*) echo $(( $(date +%s) * 1000 )) ;;  # no %N (e.g. busybox date)
		*) echo "$ms" ;;
	esac
}

release() {
	docker unpause "$@" >/dev/null 2>&1
	still_paused=""
	for container in "$@"; do
		# A container that no longer exists (e.g. recreated by a deploy) can't be paused.
		paused=$(docker inspect -f '{{.State.Paused}}' "$container" 2>/dev/null) || continue
		if [ "$paused" != "false" ]; then
			still_paused="$still_paused $container"
		fi
	done
	if [ -n "$still_paused" ]; then
		echo "Still paused after release:$still_paused — leaving watchdog (pid $watchdog) armed to unpause them" >&2
		return 1
	fi
	kill "$watchdog" 2>/dev/null
	echo "Paused for $(( $(now_ms) - paused_at ))ms: $*"
	return 0
}

finish() {
	status=$?
	trap - EXIT HUP INT TERM
	kill "$capture" 2>/dev/null
	if [ "$#" -gt 0 ] && ! release "$@"; then
		status=70
	fi
	exit "$status"
}

capture=""
if [ "$#" -gt 0 ]; then
	nohup sh -c 'sleep "$0"; docker unpause "$@"' "$watchdog_seconds" "$@" >/dev/null 2>&1 &
	watchdog=$!
	trap 'finish "$@"' EXIT
	trap 'exit 129' HUP
	trap 'exit 130' INT
	trap 'exit 143' TERM
	paused_at=$(now_ms)
	docker pause "$@" >/dev/null || exit 1
else
	echo "No running writers for $volume — already at rest, capturing without a pause"
fi

# --init so tar isn't PID 1 (which ignores SIGTERM) and the bound really stops it;
# -k hard-kills the client if it still hangs.  Run in the background and wait,
# because a trapped signal interrupts `wait` but not a foreground command.
timeout -k 5 "$capture_timeout" docker run --rm --init \
	--volume "$volume":/raw-data:ro \
	--mount "src=$mount_dir,target=$mount_dir,type=bind" \
	"$image" tar -C /raw-data -cf "$staging_path" . &
capture=$!
wait "$capture"
