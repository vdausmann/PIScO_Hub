# Cluster runbook

A condensed procedure for standing the Hub up on the production Proxmox
hosts, distilled from a full trial on a single home host. Each step here
was executed and verified; [deployment.md](deployment.md) carries the same
ground in detail, with the reasoning.

## What the trial established

Topology: Hub in an LXC, a second LXC on the same host as a worker, and a
workstation at another site reached over WireGuard. Shared storage was a
host directory bind-mounted into the containers and exported over NFS to
the remote machine.

Proven end to end, with real data:

- The C++ MPI segmenter processed 132 raw SO298 frames (2560×2560) across
  8 ranks in about 7 seconds, at 92% compression.
- A two-tool workflow ran `pisco_segment` on one machine and
  `pisco_deconvolve` on another, the second reading the first's HDF5
  through shared storage with no path written into either task. Six
  frames yielded 607 deconvolved objects.
- Dispatch over a 27 ms WireGuard link worked without special handling.
- Scheduling by tag, per-machine capacity, task timeouts, retry, and
  artifact recording all behaved as designed.

Sizing that actually worked: **Hub** 2 cores / 2 GB / 8 GB. **Tool
worker** 8 cores / 16 GB / 100 GB — the CUDA stack behind torch filled a
40 GB container completely and exhausted 8 GB of RAM.

## Decide these first

They are painful to change later.

- **One service account, same UID on every machine** (`pisco`). Ownership
  on the share is by UID, and unprivileged containers shift it by 100000.
- **One absolute path for the share** on every machine (`/mnt/pisco`).
  The Hub hands paths to tools verbatim; that is what makes remote
  execution need no file copying.
- **NFS export options.** Machines mounting over NFS cannot match the
  containers' shifted UIDs, so export with `all_squash` and `anonuid` set
  to the service account's host-side UID.
- **Tool code lives on each machine**, at identical paths
  (`/opt/pisco/tools/<repo>`), not on the share. Only data is shared.

## 1. The Hub — once

```bash
pveam update && pveam available --section system | grep debian-12
pveam download local <filename-from-above>

pct create 110 <template> --hostname pisco-hub \
    --cores 2 --memory 2048 --rootfs local-lvm:8 \
    --net0 name=eth0,bridge=vmbr0,ip=<static>/24,gw=<gw> \
    --unprivileged 1 --features nesting=1 --onboot 1
pct set 110 -mp0 /mnt/pisco,mp=/mnt/pisco       # NAS, mounted on the host
pct start 110 && pct enter 110
```

```bash
apt update && apt install -y python3 python3-venv git openssh-client locales
sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && locale-gen

adduser --system --group --home /opt/pisco-hub pisco
install -d -o pisco -g pisco /opt/pisco-hub/.ssh
runuser -u pisco -- ssh-keygen -t ed25519 -N '' -f /opt/pisco-hub/.ssh/id_ed25519
cat /opt/pisco-hub/.ssh/id_ed25519.pub          # → GitLab deploy key, and every worker
chown -R pisco:pisco /mnt/pisco

runuser -u pisco -- git clone -b toolbox-framework <hub-repo> /opt/pisco-hub/app
cd /opt/pisco-hub/app && python3 -m venv .venv
.venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e .
ln -sf /opt/pisco-hub/app/.venv/bin/hub /usr/local/bin/hub

.venv/bin/python scripts/setup_env.py
.venv/bin/python scripts/create_user.py
.venv/bin/python scripts/create_api_token.py <user> cli full     # keep this
chown -R pisco:pisco /opt/pisco-hub
```

Point `DATA_ROOT` at the share (Settings in the web UI, or the snippet in
[deployment.md](deployment.md)), then run it as a service — the unit file
is in that document. **`--workers 1` is mandatory**: the scheduler is a
thread, so a second worker process is a second scheduler.

## 2. The tool-worker template — once per cluster

Order matters; every deviation below cost us hours.

```bash
pct create 112 <template> --hostname pisco-toolworker \
    --cores 8 --memory 16384 --rootfs local-lvm:100 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --unprivileged 1 --features nesting=1 --onboot 1
pct set 112 -mp0 /mnt/pisco,mp=/mnt/pisco
pct start 112 && pct enter 112
```

```bash
apt update && apt install -y curl xz-utils git openssh-server locales
sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && locale-gen

addgroup --system pisco
adduser --system --uid <hub's uid> --ingroup pisco --home /home/pisco --shell /bin/bash pisco
install -d -o pisco -g pisco -m 700 /home/pisco/.ssh
# the Hub's public key → /home/pisco/.ssh/authorized_keys (chmod 600)
chown -R pisco:pisco /mnt/pisco

curl -fsSL https://install.determinate.systems/nix | sh -s -- install
ln -sf /nix/var/nix/profiles/default/bin/nix* /usr/local/bin/

mkdir -p /opt/pisco/tools            # copy the tool repos here, e.g. via the share
chown -R pisco:pisco /opt/pisco
```

Realize each tool's environment **as `pisco`, one at a time**, pinning it
as a profile so garbage collection cannot reclaim it:

```bash
runuser -u pisco -- bash -lc 'cd /opt/pisco/tools/<repo> && nix develop --profile /home/pisco/profiles/<name> . --command true'
# and for the segmenter, build it:
runuser -u pisco -- bash -lc 'cd /opt/pisco/tools/pisco_segmenter_cpp && nix develop . --command bash -c "mkdir -p build && cd build && cmake -DCMAKE_BUILD_TYPE=Release .. && make -j8"'
```

Verify as the Hub will reach it — over SSH, no login shell:

```bash
runuser -u pisco -- ssh -o BatchMode=yes pisco@<112-ip> \
    'nix --version && ls -d /mnt/pisco /opt/pisco/tools && echo ready'
```

Then template. Bind mounts block cloning, so detach the share first:

```bash
pct shutdown 112 && pct set 112 --delete mp0 && pct template 112
```

## 3. A worker per host

Clone onto each Proxmox host, re-attaching the share and giving it its own
SSH identity:

```bash
pct clone 112 <id> --hostname pisco-worker<N> [--target <host>]
pct set <id> -mp0 /mnt/pisco,mp=/mnt/pisco
pct start <id>
pct exec <id> -- bash -c 'rm -f /etc/ssh/ssh_host_* && dpkg-reconfigure -f noninteractive openssh-server'
pct exec <id> -- hostname -I
```

## 4. Register and tag

```bash
hub machines add worker<N> --host <ip> --user pisco \
    --tag pisco-tools [--tag gpu] --max-weight 10
hub machines list
```

`online` means the Hub reached it *and* wrote to `DATA_ROOT` — it is a
readiness check, not a ping. Tags gate placement: a tool's `requires` must
be a subset of a machine's tags, so `pisco-tools` keeps the heavy tools
off machines that lack the toolchain.

Register the toolboxes once, by the path they have on the workers:

```bash
hub providers add /opt/pisco/tools/pisco_segmenter_cpp
hub providers add /opt/pisco/tools/pisco_deconvolution_py
hub tools list
```

## 5. Verify

```bash
hub workflow create --file chain.json --watch
hub workflow status <id>        # MACHINE column shows where each task ran
hub task artifacts <task-id>
```

A chain whose second task uses `{"$from": {"task": "prev", "output":
"results"}}` and lands on a *different* machine is the test worth running:
it exercises dispatch, shared storage, path handoff and scheduling at
once.

## Traps that cost us time

| Symptom | Cause |
|---|---|
| `attempt to write a readonly database` | Setup scripts run as root; the service runs as `pisco`. `chown -R pisco:pisco /opt/pisco-hub` |
| Nix crashes with `Assertion '!awake.empty()'` | Single-user nix. Install the daemon version |
| CMake: MPI include path prefixed with the source dir | Missing locales — the wrapper's warnings are parsed as compiler flags |
| Store empties itself | `nix-collect-garbage` with no GC root. Pin shells with `nix develop --profile` |
| `Template feature is not available` | The container has a bind mount; detach it before templating |
| Tool writes beside its output directory, not into it | Tool treats the path as a filename prefix — the Hub now appends a trailing separator |
| Machine `online` but every task fails | Pre-dates the readiness probe; update the Hub |
| `nix: command not found` over SSH | Non-interactive SSH sources no profile; symlink into `/usr/local/bin` |

## Still outstanding

The three repositories are on branches (`toolbox-framework` for the Hub,
`hub-integration` for both tools) — merge them before a wider rollout, or
every machine needs the branch name remembered. GPU workers need driver
passthrough and a `/run/opengl-driver/lib` symlink, but no rebuild: the
packaged torch is already CUDA-enabled.
