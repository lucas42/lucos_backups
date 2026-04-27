# ADR-0001: NAS backup integration via ProxyJump and shell flavour adapter

**Date:** 2026-04-27
**Status:** Accepted
**Discussion:** https://github.com/lucas42/lucos_backups/issues/53

## Context

lucos_backups runs from a single container on avalon and orchestrates backups across all hosts in the lucos estate. The `Host` abstraction assumes each host is a Linux/Docker box reachable directly over SSH, with backups stored under `/srv/backups/`. This worked well for the original four-host estate (avalon, xwing, salvare, virgon-express).

Two distinct pressures have built up:

1. **Disk pressure on xwing.** The `lucos_photos_photos` volume has grown to ~3.8 GB and continues to grow. Photos was already excluded from salvare backups due to disk capacity; xwing is now also under pressure (#216). Excluding photos from xwing saves space but leaves only one off-host backup destination (avalon) — uncomfortable for `recreate_effort: huge` data.

2. **Available NAS capacity.** A QNAP NAS (aurora) sits on the home LAN with 2 TB+ of spare capacity. It is reachable from xwing over the LAN but not directly from avalon, and runs QTS 4.3.3 — a BusyBox-based environment without GNU coreutils.

Aurora's shape is meaningfully different from the existing four hosts:

- No public IPv4 or IPv6 (LAN-only on `192.168.8.143`).
- No Docker (so no source-side volume backups).
- BusyBox shell utilities, not GNU.
- Non-standard backup root (`/share/backups/` rather than `/srv/backups/`).
- QTS 4.3.3 has no firmware-native WireGuard support (5.1+ required).

Three connectivity paths were considered for getting backups from avalon to aurora:

| Path | Security profile | Code complexity | Verdict |
|---|---|---|---|
| **Public IPv6 + strict allowlist firewall** | Medium — workable with router+host firewall, ongoing operational burden, QNAP track record (DeadBolt 2022) raises the cost of any misconfiguration | Low — aurora is just another `Host` | Rejected |
| **WireGuard tunnel** (firmware-native or terminating on xwing) | High — zero exposed ports | Low — aurora is just another `Host` | Firmware-native: rejected (QTS 4.3.3 < 5.1). WireGuard-on-xwing: lower-priority alternative not pursued |
| **ProxyJump via xwing** | High — no new internet exposure | Medium — requires centralisation refactor of outbound SSH paths in `Host` | Selected |

A **relay model** (xwing rsyncs its stored backups onward to aurora) was also considered but ruled out: photos is being excluded from xwing as part of #216's resolution, so xwing won't have it to relay. The path from avalon to aurora must therefore be direct.

A **separate `BackupDestination` class** (storage-only, not inheriting `Host`) was considered as an alternative to extending `Host` with a flag. Rejected because aurora's dashboard requirements (inventory listing, disk-space monitoring, retention pruning) overlap entirely with `Host`'s existing methods — duplicating those would add code, not reduce it.

## Decision

Aurora is integrated as a **storage-only Host** in the existing lucos_backups Host model, with four coordinated changes:

### 1. Storage-only host pattern

Add an `is_storage_only` field to the host schema in lucos_configy. When `true`, `Host.getVolumes()` and `Host.getOneOffFiles()` return empty lists immediately, skipping the `docker volume ls` invocation that would fail on a non-Docker host. The host still appears as a backup destination (in the volume `backupToAll` loop) and in the dashboard inventory; only the source-side iteration is short-circuited.

### 2. Per-host backup root

The current module-level `ROOT_DIR = '/srv/backups/'` constant in `src/classes/host.py` is replaced with a per-host `backup_root` attribute, sourced from the host's lucos_configy entry. Defaults to `/srv/backups/` if absent. Threads through `getOneOffFiles`, `checkDiskSpace`, `checkBackupFiles`, `getBackups`, and the target path constructed in `Volume.backupToAll`.

### 3. Shell flavour adapter

Aurora's QTS 4.3.3 ships BusyBox utilities. Three of `Host`'s shell commands fail outright on aurora: `find -printf`, `ls --time-style=long-iso`, `df -P`. These are not single-flag differences; they require fundamentally different output-extraction strategies.

A `Shell` strategy class (new file `src/classes/shell.py`) is introduced with two implementations:

- `GnuShell` — current behaviour using `find -printf`, `ls --time-style=long-iso`, `df -P`. Used by all existing hosts.
- `BusyBoxShell` — uses Fabric's underlying `connection.sftp()` to walk the file tree and read `st_size`/`st_mtime` directly as numeric values. This avoids parsing BusyBox's date-format-quirky `ls`/`stat` output. Plain `df` is parsed by column position for disk space.

`Host.__init__` instantiates the right adapter based on a `shell_flavour` config field (default `gnu`). The four affected `Host` methods delegate shell-specific transport to the adapter; the application logic (parsing filenames into `Backup`/`Volume`, deciding what's a one-off vs volume backup) stays in `Host`.

### 4. ProxyJump via xwing with centralised outbound SSH

Aurora has no direct route from avalon. Connection from lucos_backups to aurora is via SSH ProxyJump through xwing. This requires changes in two places:

- **Inbound queries** (lucos_backups → aurora for dashboard data): the Fabric `Connection` in `Host.__init__` gets `gateway=` set when `ssh_gateway` is configured. Single code path; no partial-application risk.
- **Outbound writes** (avalon → aurora for backup data): the existing `Host.copyFileTo` and `Host.fileExistsRemotely` methods spawn raw `ssh`/`scp` subprocesses *on the source host*. These bypass the Fabric Connection's `gateway=` parameter. For aurora's backups to land via xwing, every outbound subprocess must include the appropriate `-o ProxyJump=` flag.

The April 2026 salvare experience (PR #160 added gateway support to the Fabric Connection but missed the two raw subprocess paths; PR #185 reverted it) demonstrates that **partial application of gateway logic across multiple SSH paths produces an unreliable composite.** The structural fix is to centralise outbound SSH/SCP construction through a single helper:

```python
class Host:
    def _outbound_ssh_args(self, target_host):
        """Single source of truth for outbound SSH/SCP options to a target host."""
        args = ['-o', 'StrictHostKeyChecking=no']
        if target_host.ssh_gateway:
            args += ['-o', f'ProxyJump={target_host.ssh_gateway_domain}']
        return args

    def runOnRemote(self, target_host, command):
        args = ' '.join(self._outbound_ssh_args(target_host))
        self.connection.run(f'ssh {args} {target_host.domain} {shlex.quote(command)}', ...)

    def copyTo(self, target_host, source_path, target_path):
        args = ' '.join(self._outbound_ssh_args(target_host))
        self.connection.run(f'scp {args} "{source_path}" {target_host.domain}:"{target_path}"', ...)
```

`copyFileTo` and `fileExistsRemotely` become thin wrappers calling `runOnRemote` and `copyTo`. The `target_host` parameter changes from a domain string to a `Host` object throughout the call chain (notably in `Volume.backupToAll`).

**Sequencing constraint:** the centralisation refactor and aurora's `ssh_gateway` field must land **atomically in the same PR**. Splitting them would reproduce #160's failure mode.

## Consequences

### Positive

- **Unified Host model.** Aurora reuses the existing dashboard, inventory, disk-space monitoring, and pruning logic. No parallel `BackupDestination` abstraction to maintain.
- **Centralised outbound SSH is good hygiene regardless of aurora.** Any future "all outbound calls now need flag X" change can land in one place. The structural fix #185 has been waiting for is finally in.
- **Storage-only flag is reusable.** Future destinations (S3 gateway, second NAS, tape archive, etc.) can be added by configuring the existing fields rather than introducing new abstractions.
- **No new internet exposure.** Aurora stays LAN-only. The QNAP attack surface is unchanged from before this work.
- **`recreate_effort: huge` photos volume regains a second backup destination.** Resolves the immediate driver from #216.

### Negative

- **xwing becomes a SPOF for aurora connectivity.** If xwing is down, lucos_backups cannot reach aurora for either backup writes or dashboard queries. This matches salvare's existing failure profile (gated on xwing's LAN routing in practice) and is acceptable given the setup.
- **The centralisation refactor changes a method signature.** `Host.copyFileTo` and `Host.fileExistsRemotely` change from accepting a domain string to accepting a `Host` object. Callers in `Volume.backupToAll` need updating. This is a one-PR cost but worth flagging.
- **Two adapter classes (`GnuShell`, `BusyBoxShell`) increase code surface.** Adding a future shell flavour means a new adapter class. Acceptable for the abstraction it gives — better than scattered `if shell_flavour == 'busybox'` conditionals across `Host`.
- **Photos drops to two off-source copies (avalon source + aurora destination).** With photos excluded from both salvare and xwing, the resilience floor is the bare minimum for `recreate_effort: huge`. If aurora develops reliability question marks in its first year, a fourth destination (offsite cloud or second NAS) becomes a follow-up.
- **No defence against ransomware that propagates over LAN.** Aurora is on the same physical network as the originals. Aurora gives *media diversity* (different filesystem, different OS, different vendor) but not *geographic diversity* — avalon remains the only off-premises copy.

### Follow-up actions

- **Implementation in lucos_backups** — single PR containing: `Shell` strategy classes; the centralisation refactor for outbound SSH/SCP; `is_storage_only`/`backup_root`/`shell_flavour`/`ssh_gateway` schema-aware fields in `Host`; `Volume.backupToAll` updated to pass `Host` objects rather than domain strings.
- **lucos_configy** — add aurora's host entry; remove salvare's vestigial `ssh_gateway: xwing` (left over from #160, unused since #185); set `skip_backup_on_hosts: [salvare, xwing]` on `lucos_photos_photos`.
- **Pre-implementation verification** — sysadmin to confirm the `lucos-backups` SSH key chain through xwing reaches aurora's `lucos-backups` user successfully under Fabric's `gateway=` mechanism.
- **Pruning curve for aurora** — same as every other host (no special "cold tier" yet). Revisit if observed disk usage on aurora reveals headroom for longer retention.
- **Resilience follow-up (out of scope for this ADR)** — track the two-copies-only floor for `lucos_photos_photos`; if aurora's reliability proves question-marky in the first year, raise a separate issue for a fourth destination.
