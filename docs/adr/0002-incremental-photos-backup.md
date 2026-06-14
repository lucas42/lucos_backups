# ADR-0002: Incremental backups for large immutable media volumes (photos)

**Date:** 2026-06-09
**Status:** Accepted (2026-06-09). Ratified by lucas42 on merge of lucas42/lucos_backups#319. The aurora-viability analysis lucas42 asked to settle before approval was **complete** at ratification (rsync verified present at the real backup path, §3), so this records a **single unconditional decision**, not an "if-this-then-that" fork. **Amended 2026-06-14** (post-incident lucas42/lucos#245 — rollout discipline for incremental opt-ins + orphaned-`.partial` GC; see the Amendment section at the end). The §Decision is unchanged.
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

- **C1 — Source host tooling is restricted.** The copy currently runs as a raw subprocess **on the source host (avalon)** — `self.connection.run('scp …')` via Fabric. avalon has **no rsync, no restic, no borg** (verified 2026-06-09). lucas42 prefers to **avoid host-level tooling in general** — a *soft-avoid, not a hard no* (his #311 objection on 2026-06-08 was specifically to installing rsync on avalon mid-incident; the standing preference is that host-level tooling shouldn't be rushed without weighing long-term maintenance, and that tooling already supported estate-wide is preferred). **However**, `archiveLocally()` already runs its tooling *inside a container* (`docker run alpine tar`), not on the host — so source-side tooling can be **delivered in a versioned container image** (config-as-code, rebuilt by CI) without installing anything on the avalon host. This is the lever that resolves C1: new tools belong in images, not on hosts.

- **C2 — The destination (aurora) is a dumb, non-Docker QNAP.** QTS 4.3.3, BusyBox, no Docker (ADR-0001). We cannot containerise tooling there. Anything the *destination* needs must already exist on the QNAP or be installed natively (awkward, manual, against the estate grain). This asymmetry is the crux of the mechanism choice: a mechanism whose destination needs **only SSH/SFTP** is strictly easier to operate here than one that needs a matching binary on aurora.

- **C3 — aurora shares the LAN with the originals and has a ransomware history.** ADR-0001 flagged as a standing Negative: "No defence against ransomware that propagates over LAN" (QNAP DeadBolt, 2022). aurora gives *media diversity*, not *geographic diversity*; avalon remains the only off-premises copy.

- **C4 — No truncated-backup-as-valid failure mode.** The current `scp` has no resume and writes directly to the final path — a mid-copy drop can leave a **truncated tarball** that later reads back as a "valid" backup. Whatever we choose must be **resumable** and **atomically published** (write-to-temp → rename, or an append-only repository model).

- **C5 — The first full seed must stay off the daily-cron critical path.** The one-time ~85GB seed shares the home pipe with every other volume's nightly run; it must be staged so it doesn't starve the cron.

### Candidate mechanisms

| Mechanism | Source tooling (avalon) | Destination tooling (aurora) | Dedup model | Encrypted at rest | Restore | Copy window after seed |
|---|---|---|---|---|---|---|
| **Status quo: daily full `tar`+`scp`** | container (have) | none (dumb FS) | none | no | `tar -xzf` | re-ships full **every day** ❌ |
| **GNU `tar --listed-incremental`** | container (have) | none (dumb FS) | file-level, chain | no | replay chain in order | small daily, but re-ships full on every **re-baseline** ⚠️ |
| **rsync `--link-dest` hardlink rotation** | container (deliverable) | **rsync present ✓** (verified, §3) | file-level via hardlinks | no | browse/`cp` (tool-independent) ✅ | only the day's deltas ✅ |
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

**Restore semantics — point-in-time, and what happens to deleted photos.** Because every nightly run writes a *complete, dated snapshot directory* (`…/volume-snapshots/<date>/`) rather than mutating a single mirror, restore is point-in-time by construction: browse the snapshot directory at — or nearest before — the date you want and `cp` the files out; that tree is exactly what the volume contained on that day. Deletions need no special handling and no "deleted-at" metadata. A photo deleted on a given day is **present** in every snapshot dated before the deletion and **absent** from every snapshot after it — because rsync only ever populates a fresh `<date>/` from the *current* source, so a file no longer in the volume is simply never written into the new snapshot, while the older snapshots keep their hardlink to it. The dated directories *are* the deletion record; a restore-from-date naturally includes a photo iff it still existed on that date. A deleted file therefore stays recoverable — and its bytes stay on disk via the older snapshots' hardlinks — until the last snapshot predating its deletion is pruned under the `toKeep()` retention schedule (≈ up to a year, depending where it falls in the thinning curve); after that it is permanently gone, which is the intended behaviour.

> **Implementation constraint (acceptance criterion for the post-ratification PR):** this point-in-time / deletion behaviour depends on building **per-date snapshot directories** via `--link-dest` into a fresh `<date>/`. It must **not** be implemented as a single rolling mirror with `rsync --delete` — that would propagate a deletion on the very next run and make it unrecoverable, collapsing the point-in-time property. Snapshots are immutable once published; a deletion is expressed by *absence in newer snapshots*, never by mutating an existing one.

### 2. Per-volume policy, not a global switch

This becomes a **per-volume opt-in**, not the estate default. Add a `backup_strategy` field to the volume schema in lucos_configy (default `full-snapshot`; `incremental` opts in). Rationale: for the small DB-dump / config / state volumes that are the rest of the estate, the "full" *is* tiny, full-snapshot restore is trivially a single `tar -xzf`, and the hardlink-snapshot machinery + per-volume snapshot directories would add operational surface for no benefit. The boundary is explicit: **incremental is for large, append-mostly volumes where a full snapshot exceeds the copy window or daily-full retention exceeds destination headroom.** Today that is exactly one volume: `lucos_photos_photos`.

### 3. Aurora viability (confirmed), and why restic is the considered-and-rejected alternative

`--link-dest` rests on one load-bearing assumption: **rsync present on aurora** (rsync's remote end needs the binary) and a **hardlink-capable filesystem at the actual backup-root path** — not just "somewhere on aurora". This matters because if the backup root were an SMB/NFS-mounted share, `--link-dest` would **silently degrade to full copies** (no error, no dedup, footprint reverts to daily-full). lucas42 asked for this to be **settled before approval** rather than recorded as a conditional, so the decision below is unconditional. It is settled:

> **✅ Verified 2026-06-09, re-confirmed live at the real backup path.** aurora has rsync **3.0.7** (protocol 30 — old, but `--link-dest`, `--partial`, `--append-verify` all predate it). The backup root `/share/backups/` is a **local `/dev/md0` RAID filesystem** (954.4G free), **not** an SMB/NFS mount — a hardlink test there succeeds (link count = 2), so `--link-dest` hardlinks correctly and the retention math holds. rsync works over the existing xwing→aurora ProxyJump key chain. Checked via the lucos_backups container's Fabric connection — *the same path the real backup uses* (aurora's `lucos-backups` authorized_keys has no direct agent key; a direct-access key for ad-hoc checks is a separate decision, not needed for the mechanism).

**The decision therefore stands on rsync `--link-dest` unconditionally — there is no rsync-vs-restic branch to resolve at runtime.**

**restic / borg (SFTP backend) — considered and rejected.** restic is *architecturally cleaner against C2* (it needs only SSH/SFTP on aurora, which already exists) and has three genuine edges over `--link-dest`: **confidentiality at rest**; **append-only repository access** (a restricted aurora key that can add but not delete chunks, so a compromised avalon — or LAN-propagating ransomware acting through it — cannot retroactively destroy history); and content-addressed **integrity verification** (`restic check`). It is **rejected as the mechanism** because, for our highest-`recreate_effort` data, it trades those edges for a **key-management single point of failure**: lose the repository password (a new secret in lucos_creds) and *every* photos backup is unrecoverable, and restore *requires* the restic binary plus that password — the opposite of the tool-independent `cp` restore `--link-dest` gives. For append-only immutable media, chunk-level dedup also buys little over file-level hardlinks (files are added, never rewritten in place). The confidentiality + append-only edges are real but do not outweigh restore-path fragility for the data we least want to lose. (If append-only destination protection later becomes a *hard* requirement, that is the documented trigger to revisit restic — see the integrity/append-only follow-up below. That is a future-revisit condition, not an unresolved branch in this decision.)

> **C3 framing correction (per architect review):** encryption at rest does **not** mitigate the ADR-0001 ransomware gap. That gap is about *availability/geographic diversity* (avalon is the only off-premises copy), not confidentiality — DeadBolt-class ransomware that encrypts the QNAP destroys a restic repo exactly as it destroys a plaintext snapshot tree. The one axis where restic genuinely helps *availability* is **append-only destination access**; encryption only buys *confidentiality* (who can read a stolen disk), a genuine but lesser concern for a LAN-only NAS.

**What lucas42 ratified** is a single decision, with no conditional branches: rsync `--link-dest` hardlink snapshots; source-side rsync **delivered in a versioned container image** (nothing installed on the avalon host, per C1 and the soft-avoid on host tooling); opted in **per-volume via a lucos_configy `backup_strategy` field** (§2), as a generic reusable feature for which `lucos_photos_photos` is simply the first user.

### 4. Off-cron seed staging (C5) — capability here, planning owned by the #424 migration

The mechanism **provides** a one-shot off-cron seed *capability*: a `--seed` invocation (or operator script) establishes the first `<date>/` snapshot outside the nightly cron, after which nightly runs `--link-dest` against it and ship only deltas, so the cron path never carries a full transfer.

Per lucas42's direction, the **planning and execution** of the actual ~85GB photos seed — staging it off-peak so it doesn't starve the nightly cron, and the verified-restore-before-import gate — is **not a standalone step in this ADR**. It is folded into the Google→lucOS migration choreography: the lucas42/lucos_photos#424 epic, concretely staged in the **lucas42/lucos_photos#427** cutover ticket (confirmed with the architect, who holds the #424 design context). This ADR records only that rsync `--link-dest` *supports* a `--seed`; lucas42/lucos_photos#427 owns *when and how* the photos seed runs and the restore-test gate.

## Consequences

### Positive

- **Copy window permanently solved.** Post-seed, the nightly photos transfer is the day's deltas — minutes. The migration no longer re-breaks backups, and the mechanism scales to the 500GB trajectory without the copy window degrading.
- **Retention footprint collapses.** At ~85GB with ~22 hardlinked daily snapshots: ≈ **~85GB + a few GB of deltas**, vs ~1.87TB daily-full — comfortably inside aurora's 954.4G with years of runway. At 500GB: ≈ **~520GB** vs an impossible ~11TB. (Hardlink snapshots cost one full plus accumulated deltas, *not* N× full.)
- **No truncated-backup-as-valid failure mode** (C4): resumable transfer + atomic snapshot publish, by construction.
- **Tool-independent, secret-free restore** for the highest-`recreate_effort` volume.
- **No host-binary install on avalon** — source tooling ships in a versioned image, consistent with the existing `archiveLocally` container pattern and lucas42's no-new-host-binary preference (C1).

### Negative

- **rsync required on aurora** (C2) — **verified present: rsync 3.0.7 with hardlink support at `/share/backups/`, 2026-06-09 (§3)**. This was the load-bearing assumption and it holds; had it failed, the fallback was restic-over-SFTP.
- **No append-only destination protection, and no confidentiality at rest** (C3). A compromised avalon — or LAN-propagating ransomware acting through it — can delete or overwrite the snapshots on aurora, and the snapshots sit in plaintext. Note this does **not** widen the ADR-0001 ransomware gap (that gap is geographic/availability — avalon stays the only off-premises copy regardless); it is the residual that `--link-dest` does not close. Accepting it is part of the sign-off; restic-over-SFTP with an append-only key is the escalation if either property becomes a hard requirement.
- **Hardlink snapshots share inodes → shared corruption fate.** Because unchanged files are the *same inode* across snapshots, on-disk bit-rot in a shared block corrupts that file in *every* snapshot referencing it — there is no per-snapshot redundancy and no `restic check`-style content verification. So the tool-independent-restore win is **not** clean dominance over restic: we trade restic's integrity-verification for browse-and-`cp` simplicity. Mitigated by the integrity-sweep follow-up below, not by the mechanism itself.
- **Two backup code paths** (`full-snapshot` and `incremental`) increase surface in `Volume`. Mitigated by the explicit per-volume boundary (§2) — only volumes that genuinely need it opt in; today that's one.
- **Restore of an incremental volume is a different runbook** from the tar volumes (browse the snapshot dir + `cp`, vs `tar -xzf`). Must be documented in `restore-volume.sh` / README before cutover.
- **Snapshot pruning is new logic.** Hardlink-rotated snapshots need a retention/prune step (keep N dailies, thin older) distinct from the existing per-file dated-tarball pruning. Deleting an old snapshot directory is safe (hardlinks keep shared inodes alive until the last reference goes), but the prune curve must be defined.

### Follow-up actions

- **Step zero (aurora viability): ✅ DONE 2026-06-09.** Verified aurora has rsync 3.0.7 and a hardlink-capable local FS at the real backup-root path `/share/backups/` (`/dev/md0`, not an NFS/SMB mount), and that rsync works over the ProxyJump path (see §3). The mechanism is confirmed viable on rsync. This was the gate lucas42 asked to clear before approval; it has passed, and lucas42 has now ratified, so the implementation PR may proceed on rsync `--link-dest`.
- **Integrity sweep (new, from the shared-inode trade above):** add a periodic checksum sweep over the aurora snapshots (e.g. compare against a manifest, or `rsync -c` dry-run from source) to detect bit-rot that hardlink snapshots cannot self-heal and have no `restic check` equivalent for. Raise as its own issue; calibrate cadence to cost — this is the price of choosing browse-and-`cp` restore over restic's built-in verification.
- **lucos_configy:** add `backup_strategy: incremental` to `lucos_photos_photos`; schema default `full-snapshot` for all others.
- **lucos_backups implementation (separate PR, post-sign-off):** container image carrying rsync; `incremental` path in `Volume` (`--link-dest` snapshot rotation, `.partial`→rename atomic publish); snapshot-aware pruning; off-cron `--seed` path; restore runbook.
- **Verified restore + seed sequencing (owned by lucas42/lucos_photos#427, under the #424 epic):** an end-to-end backup **and** an actual **restore-test** of the seed — proving the mechanism is intact, not merely that a backup file exists — plus the off-peak seed run, are sequenced as migration choreography in lucas42/lucos_photos#427 and must complete **before** the 78GB import (prove it on the *current* volume first). This ADR provides the mechanism + `--seed` capability; lucas42/lucos_photos#427 owns the run.
- **Resilience floor unchanged from ADR-0001:** photos still has only two copies (avalon source + aurora). The "fourth destination if aurora proves question-marky" item from ADR-0001 still stands and is out of scope here.

## Amendment — 2026-06-14: rollout discipline for incremental opt-ins (post-incident)

The implementation (lucas42/lucos_backups#324) and its first live seed surfaced two latent bugs **in series**: a ProxyJump host-key bug (#327 → fix #329) and an atomic-publish shell-quoting bug that stranded transferred data in `<date>.partial/` (#330 → fix #331), across v1.1.13 → v1.1.15. Both sat in exactly the surface §1's "Implementation wrinkle" flagged as the hard one — the container carrying the SSH key / `known_hosts` / ProxyJump config to do the WAN transfer itself. Written up in the incident report lucas42/lucos#245.

This amendment records the durable lessons. It **does not change the §Decision** — it adds to Consequences and Follow-up.

### Added to Consequences (Negative)

- **The incremental remote-execution path is structurally un-CI-testable.** rsync-from-an-ephemeral-container, over a ProxyJump gateway, to aurora cannot be exercised in CI without the real gateway and destination reachable — so it was first exercised only by the off-cron seed against production. A path that is only ever run live **hides bugs in series**: each fix unblocks the next failure (here, the publish bug was unreachable until the host-key bug was fixed). This is an inherent property of the mechanism under C1+C2 (containerised source tooling delivering its own WAN egress, to a dumb destination, over a home WAN), not a defect to be engineered away. Crucially, both bugs lived in the **source-host → destination-host transport path** (host-key trust to the ProxyJump gateway; remote-shell quoting on the destination), not in anything volume-specific — so this un-CI-testable risk is **per host / per transport path**: it recurs when a *new source host* or a *new destination* is onboarded, **not** when another volume is added on a host whose path is already proven. The two bugs were *execution of a foreseen risk*, not a blind spot — but the foresight in §1 did not, on its own, prevent them.

### Added to Follow-up actions

- **Rollout discipline — scoped to new hosts / transport paths, not new volumes** (per lucas42's review, 2026-06-14). Because the serial bugs were properties of the *source-host → destination-host transport path* (above), the supervised-rollout care is **per-host / per-path**, not per-volume. Onboarding incremental backups on a **new source host**, or to a **new destination**, is where it is warranted: roll it out via a **supervised, off-cron seed run** with a **monitoring watch on `create-backups`**, *expecting* serial first-live bug discovery and fixing forward. This is the deliberate process substitute for the CI/staging harness that was **risk-accepted as not worth building** (non-deterministic, needs the real gateway + destination, and the failure mode is an internal backup delayed ≤1 day that `create-backups` already catches — see lucas42/lucos#245). Adding a **new volume on a host whose incremental path is already proven reuses that path and needs no such care** — `lucos_photos_photos` over avalon→aurora is the worked precedent that proves that path.
- **The `--seed` capability still earns its keep for any *large* new volume — for a resource reason, not bug-discovery.** Even on an already-proven path, a large volume's one-time full seed should be run off-cron (the §4 / C5 capability) so the initial full transfer doesn't starve the nightly cron's other volumes. So `--seed` has two independent justifications: *serial-bug-discovery* (first incremental backup on a new host/path) and *copy-window/resource* (any large first seed, on any host).
- **Orphaned `.partial` garbage-collection** (tracked: lucas42/lucos_backups#333). The "Snapshot pruning is new logic" item above covers *final* snapshots only. A run that fails **after** rsync lands data but **before** the atomic publish leaves a `<date>.partial/` behind; a same-date retry resumes it (`--partial`), but a failure fixed on a **later** date orphans the prior `.partial` permanently — `_latest_snapshot_date` ignores `.partial` and nothing GCs it. Per-orphan cost is bounded (hardlinked via `--link-dest` to the previous final ≈ one day's deltas, not a full copy), but the count is **unbounded** over repeated cross-date failures on the capacity-capped aurora NAS. The 2026-06-08 incident *exposed* this gap rather than triggering a leak (#330 was fixed same-date 08:42→08:45, so the seed almost certainly resumed-and-published the same-date `.partial`). The prune step should GC stale `.partial/` dirs alongside thinning final snapshots.
