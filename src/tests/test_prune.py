"""
Unit tests for Backup.prune() and Backup.toKeep().

Covers:
- The rm command sent to the host connection uses `rm -f` (not `rm -vf`),
  so it works on BusyBox v1.01 as well as GNU coreutils.
- Dryrun mode uses `ls` / `echo` and never calls `rm`.
- prune() returns the correct count of deleted instances.
- toKeep() age-banding logic.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, call
import pytest
import sys
import importlib


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_stored_host():
	host = MagicMock()
	host.name = "aurora"
	host.connection = MagicMock()
	host.connection.run = MagicMock()
	return host


def make_backup(stored_host, instances):
	"""Create a Backup with the given list of (name, date, size, path) tuples."""
	# Import fresh each time so sys.modules noise from other tests doesn't matter
	from classes.backup import Backup
	b = Backup(stored_host, "source_host", "volume", "test_volume")
	for name, d, size, path in instances:
		b.addInstance(name, d, size, path)
	return b


# ---------------------------------------------------------------------------
# prune() — rm command format (the regression guard for #257)
# ---------------------------------------------------------------------------

class TestPruneRmCommand:
	"""Regression guard: rm must not use -v (BusyBox v1.01 rejects it)."""

	def setup_method(self):
		sys.modules.pop("classes.backup", None)

	def test_rm_does_not_use_v_flag(self):
		"""prune() must send `rm -f` not `rm -vf` to the stored host."""
		host = make_stored_host()
		# Two instances: one fresh (kept), one old enough to prune (28 days old, day 1 — not kept)
		old_date = date.today() - timedelta(days=28)
		# day=1 → 1 % 6 != 0 → not kept
		old_date = old_date.replace(day=1)
		fresh_date = date.today() - timedelta(days=1)
		b = make_backup(host, [
			("old", old_date, "100M", "/backups/old.tar.gz"),
			("fresh", fresh_date, "100M", "/backups/fresh.tar.gz"),
		])

		b.prune(dryrun=False)

		calls = host.connection.run.call_args_list
		assert len(calls) == 1, "Expected exactly one rm call"
		cmd = calls[0][0][0]
		assert "rm -f" in cmd, "rm must use -f flag (no -v)"
		assert "-vf" not in cmd, "rm must NOT include -v flag (BusyBox v1.01 rejects it)"
		assert "/backups/old.tar.gz" in cmd

	def test_dryrun_does_not_call_rm(self):
		"""prune(dryrun=True) must use ls/echo and never call rm."""
		host = make_stored_host()
		old_date = date.today() - timedelta(days=28)
		old_date = old_date.replace(day=1)
		fresh_date = date.today() - timedelta(days=1)
		b = make_backup(host, [
			("old", old_date, "100M", "/backups/old.tar.gz"),
			("fresh", fresh_date, "100M", "/backups/fresh.tar.gz"),
		])

		b.prune(dryrun=True)

		calls = host.connection.run.call_args_list
		assert len(calls) == 1
		cmd = calls[0][0][0]
		assert "rm" not in cmd, "dryrun must not call rm"
		assert "ls" in cmd or "echo" in cmd

	def test_prune_returns_correct_count(self):
		"""prune() returns the number of files actually deleted."""
		host = make_stored_host()
		# Three old instances, two of which should be pruned (day 1 and day 2 — both % 6 != 0)
		base = date.today() - timedelta(days=14)
		d1 = base.replace(day=1)
		d2 = base.replace(day=2)
		d3 = base.replace(day=6)   # 6 % 6 == 0 → kept
		b = make_backup(host, [
			("a", d1, "10M", "/backups/a.tar.gz"),
			("b", d2, "10M", "/backups/b.tar.gz"),
			("c", d3, "10M", "/backups/c.tar.gz"),
		])

		count = b.prune(dryrun=False)
		assert count == 2

	def test_prune_skips_all_fresh_instances(self):
		"""No rm calls when all instances are within the first week (always kept)."""
		host = make_stored_host()
		fresh1 = date.today() - timedelta(days=1)
		fresh2 = date.today() - timedelta(days=2)
		b = make_backup(host, [
			("a", fresh1, "10M", "/backups/a.tar.gz"),
			("b", fresh2, "10M", "/backups/b.tar.gz"),
		])

		count = b.prune(dryrun=False)
		assert count == 0
		host.connection.run.assert_not_called()

	def test_lone_instance_never_pruned(self):
		"""A backup with only one instance must never be deleted."""
		host = make_stored_host()
		very_old = date(2020, 1, 1)
		b = make_backup(host, [
			("only", very_old, "10M", "/backups/only.tar.gz"),
		])

		count = b.prune(dryrun=False)
		assert count == 0
		host.connection.run.assert_not_called()


# ---------------------------------------------------------------------------
# toKeep() — age-band logic
# ---------------------------------------------------------------------------

class TestToKeep:
	"""toKeep() must apply the documented age-banding rules."""

	def setup_method(self):
		sys.modules.pop("classes.backup", None)

	def _backup_with_two_instances(self, d1, d2):
		"""Helper: backup with two instances so lone-instance shortcut doesn't apply."""
		host = make_stored_host()
		return make_backup(host, [
			("a", d1, "10M", "/backups/a.tar.gz"),
			("b", d2, "10M", "/backups/b.tar.gz"),
		])

	def test_first_week_always_kept(self):
		"""Instances < 7 days old are always kept."""
		b = self._backup_with_two_instances(
			date.today() - timedelta(days=3),
			date.today() - timedelta(days=1),
		)
		for inst in b.instances:
			assert b.toKeep(inst) is True

	def test_week_1_to_5_keeps_multiples_of_6(self):
		"""Between 1 and 5 weeks: keep when day % 6 == 0."""
		base = date.today() - timedelta(weeks=2)
		d_kept = base.replace(day=6)
		d_pruned = base.replace(day=1)
		b = self._backup_with_two_instances(d_kept, d_pruned)
		inst_kept = next(i for i in b.instances if i["date"] == d_kept)
		inst_pruned = next(i for i in b.instances if i["date"] == d_pruned)
		assert b.toKeep(inst_kept) is True
		assert b.toKeep(inst_pruned) is False

	def test_week_5_to_52_keeps_sixth_of_month(self):
		"""Between 5 and 52 weeks: keep when day == 6."""
		base = date.today() - timedelta(weeks=10)
		d_kept = base.replace(day=6)
		d_pruned = base.replace(day=7)
		b = self._backup_with_two_instances(d_kept, d_pruned)
		inst_kept = next(i for i in b.instances if i["date"] == d_kept)
		inst_pruned = next(i for i in b.instances if i["date"] == d_pruned)
		assert b.toKeep(inst_kept) is True
		assert b.toKeep(inst_pruned) is False

	def test_over_52_weeks_keeps_sixth_of_january_only(self):
		"""After 52 weeks: keep only day==6, month==January."""
		d_kept = date(2023, 1, 6)
		d_pruned_wrong_day = date(2023, 1, 7)
		d_pruned_wrong_month = date(2023, 6, 6)
		b = self._backup_with_two_instances(d_kept, date(2023, 7, 6))
		# Manually create a three-instance backup to test all cases
		host = make_stored_host()
		b2 = make_backup(host, [
			("a", d_kept, "10M", "/backups/a.tar.gz"),
			("b", d_pruned_wrong_day, "10M", "/backups/b.tar.gz"),
			("c", d_pruned_wrong_month, "10M", "/backups/c.tar.gz"),
		])
		inst_a = next(i for i in b2.instances if i["date"] == d_kept)
		inst_b = next(i for i in b2.instances if i["date"] == d_pruned_wrong_day)
		inst_c = next(i for i in b2.instances if i["date"] == d_pruned_wrong_month)
		assert b2.toKeep(inst_a) is True
		assert b2.toKeep(inst_b) is False
		assert b2.toKeep(inst_c) is False
