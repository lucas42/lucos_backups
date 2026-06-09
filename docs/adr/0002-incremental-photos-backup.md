# ADR-0002: Incremental backups for large immutable media volumes (photos)

**Date:** 2026-06-09
**Status:** Proposed — pending lucas42 sign-off on the mechanism. The §3 aurora-viability question is now **resolved** (verified 2026-06-09, see §3), so all three sign-off asks are answerable as definite choices.
**Discussion:** https://github.com/lucas42/lucos_backups/issues/318
**Forcing function:** https://github.com/lucas42/lucos_photos/issues/424 (Google Photos import ~13×'s the `photos` volume)
**Throughput data point:** https://github.com/lucas42/lucos_backups/issues/309 (closed copy-timeout incident)

## Context

`lucos_backups` currently backs up every volume the same way (`src/classes/volume.py`):

1. `archiveLocally()` runs `docker run … alpine tar -C /raw-data -czf …` to produce a **dated full tarball** of the whole volume on the source host (avalon), under `{backup_root}local/volume/{name}.{date}.tar.gz`. Timeout: 600s.
2. `backupToAll()` `scp`s that whole tarball to each destination host (for `lucos_photos_photos`: only **aurora**, since `skip_backup_on_hosts: [salvare, xwing]`), via SSH ProxyJump through xwing (per ADR-0001). Timeout: 7200s (raised from 600s by the #309 fix).

This works for the rest of the estate, where volumes are small (DB dumps, config, state files). It is **pathological for `lucos_photos_photos`**, and the Google Photos migration makes it acute.

### The forcing function

The migration (lucas42/lucos_photos#424) adds ~78GB to `photos`: **~6.6GB → ~85GB**, on a trajectory to ~500GB. Two structural problems, neither of which is about disk on the source:

1. **The WAN copy window is the binding constraint.** #309 timed out copying the **6.6GB** tarball at the old 600s cap. The fix raised the cap to 7200s, but the implied throughput on the avalon→xwing→aurora home-WAN leg is **<~11 MB/s**. At that rate an ~85GB tarball takes **~2.1 hours — already over the freshly-raised 7200s (2h) cap.** A full-snapshot model re-breaks on day one of the migration. (The local `tar -czf` step has its own 600s cap, which ~85GB very likely also exceeds — a second, independent breakage; this one is a projection, not a measured timeout.)

2. **Full snapshots of immutable media are wasteful by ~99%.** JPEG/H.264 is already compression-saturated, so `tar -czf` gains almost nothing; each daily instance ≈ full size. `photos` is an essentially **append-only, immutable** store — we re-ship ~99%-identical bytes every night. At the 500GB trajectory, daily-full retention is **~11TB** on the destination. aurora's free space is **954.4G** (lucas42, 2026-06-08) — daily-full at *post-migration* size already needs ~1.87TB and **does not fit**.

> **Retained-instance count (~22).** Sourced from the `toKeep()` retention schedule in `src/classes/backup.py`: every instance for the first week, then every sixth day out to 5 weeks, then the 6th of each month for the first year, then annually. That lands on the order of ~20–25 retained instances across the first year — ~22 is the round figure used throughout this ADR.

This is the textbook case for **incremental / content-addressed** backup. The decision is about *which* incremental mechanism, under the estate's specific constraints.

### Constraints that rule the decision

These are what make this a real decision rather than "just use restic":

- **C1 — Source host tooling is restricted.** The copy currently runs as a raw subprocess **on the source host (avalon)** — `self.connection.run('scp …')` via Fabric. avalon has **no rsync, no restic, no borg** (verified 2026-06-09). lucas42 declined installing rsync on avalon during #311 (2026-06-08), stating a preference for tooling already supported estate-wide and for surfacing host-provisioning cost before adding a host dependency. **However**, `archiveLocally()` already runs its tooling *inside a container* (`docker run alpine tar`), not on the host — so source-side tooling can be **delivered in a versioned container image** (config-as-code, rebuilt by CI) without installing anything on the avalon host. This is the lever that resolves C1: new tools belong in images, not on hosts.

- **C2 — The destination (aurora) is a dumb, non-Docker QNAP.** QTS 4.3.3, BusyBox, no Docker (ADR-0001). We cannot containerise tooling there. Anything the *destination* needs must already exist on the QNAP or be installed natively (awkward, manual, against the estate grain). This asymmetry is the crux of the mechanism choice: a mechanism whose destination needs **only SSH/SFTP** is strictly easier to operate here than one that needs a matching binary on aurora.

- **C3 — aurora shares the LAN with the originals and has a ransomware history.** ADR-0001 flagged as a standing Negative: "No defence against ransomware that propagates over LAN" (QNAP DeadBolt, 2022). aurora gives *media diversity*, not *geographic diversity*; avalon remains the only off-premises copy.

- **C4 — No truncated-backup-as-valid failure mode.** The current `scp` has no resume and writes directly to the final path — a mid-copy drop can leave a **truncated tarball** that later reads back as a "valid" backup. Whatever we choose must be **resumable** and **atomically published** (write-to-temp → rename, or an append-only repository model).

- **C5 — The first full seed must stay off the daily-cron critical path.** The one-time ~85GB seed shares the home pipe with every other volume's nightly run; it must be staged so it doesn't starve the cron.

### Candidate mechanisms

| Mechanism | Source tooling (avalon) | Destination tooling (aurora) | Dedup model | Encrypted at rest | Restore | Copy window after seed |
|---|---|---|---|---|---|---|
| **Status quo: daily full `tar`+`scp`** | container (have) | none (dumb FS) | none | no | `tar -xzf` | re-ships full **every day** ❌ |
| **GNU `tar --listed-incremental`** | container (have) | none (dumb FS) | file-level, chain | no | replay chain in order | small daily, but re-ships full on every **re-baseline** ⚠️ |
| **rsync `--link-dest` hardlink rotation** | container (deliverable) | **rsync required** ⚠️ (C2) | file-level via hardlinks | no | browse/`cp` (tool-independent) ✅ | only the day's deltas ✅ |
| **restic / borg (SFTP backend)** | container (deliverable) | **SSH/SFTP only** ✅ (C2) | chunk-level content-addressed | **yes** ✅ (C3) | requires restic + repo password ⚠️ | only changed chunks ✅ |

Notes on the losers:

- **Status quo** is the forcing function; rejected.
- **`tar --listed-incremental`** is attractive because it needs *no new tool anywhere* (GNU tar in the alpine container; aurora stays a dumb file store). But it loses on two counts: (a) bounding the increment chain requires periodically taking a fresh level-0 full, and **every re-baseline re-ships ~85GB+ over the WAN — the copy-window problem returns on a schedule**; (b) restore requires replaying the full + every increment in order, and a single missing/corrupt increment breaks the chain. Good enough as a *no-container-tooling fallback*, not good enough as the answer.

## Decision

Adopt **rsync `--link-dest` hardlink-rotated snapshots** for large immutable media volumes, with the source-side rsync **delivered in a container image** (installing nothing on the avalon host), as a **per-volume opt-in policy**.

### 1. Mechanism: rsync `--link-dest`

For an opted-in volume, replace the tar+scp path with: mount the raw volume (as `archiveLocally` already does), and `rsync -a --partial --append-verify --link-dest=<previous-snapshot> <volume>/ aurora:<backup_root>/host/<src>/volume-snapshots/<date>/` over the existing ProxyJump path.

> **Implementation wrinkle — this is more than "extending the `docker run alpine tar` pattern."** Today the container step is **pure-local** (it produces a tarball on disk); the network egress is the *host-side* `scp` run via Fabric. `--link-dest` collapses those: rsync does the WAN transfer **from inside the container**, so the container must carry the SSH private key, `known_hosts`, and the ProxyJump config (e.g. mount `~/.ssh` read-only and supply an `--rsh` wrapper) and reach aurora itself. Entirely doable, but it must be **designed into the implementation PR** — it is not free, and it changes the container's secret-handling surface vs the tar step.

- **Incremental transfer:** only new/changed files cross the WAN. After the one-time seed, the daily transfer is just that day's new photos (minutes, not hours) — permanently retiring the copy-window failure (the forcing function). Unlike tar-incremental, there is **never** a re-baseline that re-ships the full.
- **Hardlink retention:** unchanged files in `<date>/` are hardlinks to the previous snapshot, costing **zero extra bytes**. N daily snapshots cost ≈ one full + the bytes actually added across the window.
- **Resumable + atomic (C4):** `--partial --append-verify` resumes an interrupted transfer; rsync into a `<date>.partial/` directory then rename to `<date>/` only on success gives atomic publish. A failed run can never masquerade as a complete snapshot.
- **Tool-independent restore:** a snapshot is a plain directory tree on aurora. Restore is `cp`/`scp` — no special tool, no password, no chain replay. For our highest-`recreate_effort` data, *restoreability without depending on a specific tool or secret* is the property that matters most.

### 2. Per-volume policy, not a global switch

This becomes a **per-volume opt-in**, not the estate default. Add a `backup_strategy` field to the volume schema in lucos_configy (default `full-snapshot`; `incremental` opts in). Rationale: for the small DB-dump / config / state volumes that are the rest of the estate, the "full" *is* tiny, full-snapshot restore is trivially a single `tar -xzf`, and the hardlink-snapshot machinery + per-volume snapshot directories would add operational surface for no benefit. The boundary is explicit: **incremental is for large, append-mostly volumes where a full snapshot exceeds the copy window or daily-full retention exceeds destination headroom.** Today that is exactly one volume: `lucos_photos_photos`.

### 3. Open question requiring sign-off: rsync on aurora (C2), and the restic alternative

`--link-dest` requires **rsync present on aurora** (rsync's remote end needs the binary) and a **hardlink-supporting filesystem** there. QNAP QTS ships rsync for its own backup features, so this is *likely* satisfied — but it is **unverified** (the agent's SSH key could not reach aurora directly on 2026-06-09; this is sysadmin's to confirm, per the ADR-0001 precedent of sysadmin verifying the key chain). **Step zero is to confirm aurora has rsync ≥3 *and* a hardlink-capable filesystem at the actual backup-root path** — not just "somewhere on aurora". This matters: if the backup root is an SMB/NFS-mounted share, `--link-dest` **silently degrades to full copies** (no error, no dedup, retention footprint reverts to daily-full). The check must run at the real target path.

> **✅ VERIFIED 2026-06-09 (sysadmin).** rsync **3.0.7** present on aurora (≥3 ✓; old but supports every flag this ADR uses — `--link-dest`, `--partial`, `--append-verify` all predate it). A hardlink test at the real backup root `/share/backups/` succeeded (link count = 2), confirming it is **not** an SMB/NFS mount that would degrade `--link-dest`. An rsync invocation over the existing xwing→aurora ProxyJump key chain also works. **Conclusion: ask (b) resolves to rsync-on-aurora; the restic-over-SFTP fallback is not needed.** (One operational note recorded by sysadmin: aurora's `lucos-backups` authorized_keys has no direct agent key, so the check ran via the lucos_backups container's fabric connection — the same path the real backup uses; a direct-access key for future ad-hoc checks is a separate decision, not required for the mechanism.)

If aurora cannot host rsync, the recommended fallback is **restic to an SFTP backend** — and this is the one dimension on which restic is *architecturally cleaner*: per C2 it needs **only SSH/SFTP on aurora** (which already exists via the ProxyJump path), keeping all the smarts on the containerised avalon side and treating aurora as the dumb target it is. restic's genuine edges over `--link-dest` are: **confidentiality at rest** (the snapshots are encrypted on the QNAP); **append-only repository access** — a restricted aurora key that can add but not delete chunks, so a compromised avalon (or LAN-propagating ransomware acting through it) cannot retroactively destroy history; and content-addressed **integrity verification** (`restic check`).

> **Correction on the C3 framing (per architect review):** encryption at rest does **not** mitigate the ADR-0001 ransomware gap. That gap is about *availability/geographic diversity* (avalon is the only off-premises copy), not confidentiality — DeadBolt-class ransomware that encrypts the QNAP destroys a restic repo exactly as it destroys a plaintext snapshot tree. The real lever for the *availability* side is **append-only destination access** (above), which is the one axis where restic has an edge `--link-dest` cannot match natively. Encryption only buys *confidentiality* (who can read a stolen disk), a genuine but lesser concern for a LAN-only NAS.

restic is **not** recommended as the primary because, for our most critical data, it trades those edges for a **key-management single point of failure**: lose the repository password (a new secret in lucos_creds) and *every* photos backup is unrecoverable, and restore *requires* the restic binary and that password — the opposite of the tool-independent `cp` restore that `--link-dest` gives. For append-only immutable media, restic's chunk-level dedup also buys little over file-level hardlinks (files are added, never rewritten in place). The confidentiality + append-only edges are real, but do not outweigh the restore-path fragility for the data we least want to lose. (If append-only destination protection later becomes a hard requirement, that is the trigger to revisit restic — see the integrity/append-only follow-up below.)

**Sign-off asks for lucas42:** (a) confirm rsync-in-a-container on the *source* is an acceptable reading of the no-new-host-binary preference (nothing installed on avalon; tool versioned in an image); (b) confirm rsync on *aurora* is acceptable (or direct us to the restic-over-SFTP fallback instead); (c) ratify rsync `--link-dest` as the mechanism vs the restic alternative weighed above.

### 4. Off-cron seed staging (C5)

The one-time ~85GB seed (~2.1h on the WAN) runs as a **manual, off-peak, one-shot** outside the nightly cron — a `restore-volume.sh`-style operator script, or a `--seed` flag on the backup invocation, that establishes the first `<date>/` snapshot. Subsequent nightly runs `--link-dest` against it and ship only deltas, so the cron path never carries a full transfer. The seed must complete and a restore be verified **before** the 78GB import runs (acceptance criterion / migration blocker).

## Consequences

### Positive

- **Copy window permanently solved.** Post-seed, the nightly photos transfer is the day's deltas — minutes. The migration no longer re-breaks backups, and the mechanism scales to the 500GB trajectory without the copy window degrading.
- **Retention footprint collapses.** At ~85GB with ~22 hardlinked daily snapshots: ≈ **~85GB + a few GB of deltas**, vs ~1.87TB daily-full — comfortably inside aurora's 954.4G with years of runway. At 500GB: ≈ **~520GB** vs an impossible ~11TB. (Hardlink snapshots cost one full plus accumulated deltas, *not* N× full.)
- **No truncated-backup-as-valid failure mode** (C4): resumable transfer + atomic snapshot publish, by construction.
- **Tool-independent, secret-free restore** for the highest-`recreate_effort` volume.
- **No host-binary install on avalon** — source tooling ships in a versioned image, consistent with the existing `archiveLocally` container pattern and lucas42's no-new-host-binary preference (C1).

### Negative

- **rsync required on aurora** (C2) — likely present on QNAP but must be verified; if absent, we fall to the restic-over-SFTP path. This is the load-bearing assumption (§3).
- **No append-only destination protection, and no confidentiality at rest** (C3). A compromised avalon — or LAN-propagating ransomware acting through it — can delete or overwrite the snapshots on aurora, and the snapshots sit in plaintext. Note this does **not** widen the ADR-0001 ransomware gap (that gap is geographic/availability — avalon stays the only off-premises copy regardless); it is the residual that `--link-dest` does not close. Accepting it is part of the sign-off; restic-over-SFTP with an append-only key is the escalation if either property becomes a hard requirement.
- **Hardlink snapshots share inodes → shared corruption fate.** Because unchanged files are the *same inode* across snapshots, on-disk bit-rot in a shared block corrupts that file in *every* snapshot referencing it — there is no per-snapshot redundancy and no `restic check`-style content verification. So the tool-independent-restore win is **not** clean dominance over restic: we trade restic's integrity-verification for browse-and-`cp` simplicity. Mitigated by the integrity-sweep follow-up below, not by the mechanism itself.
- **Two backup code paths** (`full-snapshot` and `incremental`) increase surface in `Volume`. Mitigated by the explicit per-volume boundary (§2) — only volumes that genuinely need it opt in; today that's one.
- **Restore of an incremental volume is a different runbook** from the tar volumes (browse the snapshot dir + `cp`, vs `tar -xzf`). Must be documented in `restore-volume.sh` / README before cutover.
- **Snapshot pruning is new logic.** Hardlink-rotated snapshots need a retention/prune step (keep N dailies, thin older) distinct from the existing per-file dated-tarball pruning. Deleting an old snapshot directory is safe (hardlinks keep shared inodes alive until the last reference goes), but the prune curve must be defined.

### Follow-up actions

- **Step zero (sysadmin): ✅ DONE 2026-06-09.** Verified aurora has rsync 3.0.7 and a hardlink-capable FS at the real backup-root path `/share/backups/`, and that rsync works over the ProxyJump path (see §3). The mechanism is confirmed viable on rsync; the restic-over-SFTP fallback is not triggered. (This was the gate ahead of any implementation; it has passed, so the implementation PR may proceed on rsync `--link-dest` once lucas42 ratifies.)
- **Integrity sweep (new, from the shared-inode trade above):** add a periodic checksum sweep over the aurora snapshots (e.g. compare against a manifest, or `rsync -c` dry-run from source) to detect bit-rot that hardlink snapshots cannot self-heal and have no `restic check` equivalent for. Raise as its own issue; calibrate cadence to cost — this is the price of choosing browse-and-`cp` restore over restic's built-in verification.
- **lucos_configy:** add `backup_strategy: incremental` to `lucos_photos_photos`; schema default `full-snapshot` for all others.
- **lucos_backups implementation (separate PR, post-sign-off):** container image carrying rsync; `incremental` path in `Volume` (`--link-dest` snapshot rotation, `.partial`→rename atomic publish); snapshot-aware pruning; off-cron `--seed` path; restore runbook.
- **Verified restore (acceptance criterion):** an end-to-end backup **and** restore of `photos` at current (~6.6GB) size, proving the mechanism, **before** the 78GB import.
- **Resilience floor unchanged from ADR-0001:** photos still has only two copies (avalon source + aurora). The "fourth destination if aurora proves question-marky" item from ADR-0001 still stands and is out of scope here.
