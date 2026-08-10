# Deploying the Hub on a Proxmox LXC

The Hub is a small Flask server plus three background threads. It needs no
GPU and very little CPU — it schedules work, it does not do the work — so
a modest container is enough. What it *does* need is to see the shared
storage at the same paths its workers see, and to reach those workers over
SSH.

This describes the Hub container. Workers are a separate step.

## Sizing

2 cores, 2 GB RAM, 8 GB disk is comfortable. The database is SQLite and
stays small; logs and per-task settings files live in the container, while
results go to shared storage.

## 1. Create the container

First make sure a container template is present — `pct create` fails with
"volume ... does not exist" if it is not, and the version in the filename
moves, so take the exact name from the mirror rather than copying one:

```bash
pveam update
pveam list local                                    # already have one?
pveam available --section system | grep debian-12   # exact current filename
```

If it is not listed yet, download it, substituting the filename that
command printed — the version moves, so do not copy one from here:

```bash
pveam download local debian-12-standard_12.12-1_amd64.tar.zst
```

Templates need a storage with `vztmpl` in its content types — `local`
normally has it, `local-lvm` cannot hold them.

Then, on the Proxmox host:

```bash
pct create 110 local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst \
    --hostname pisco-hub \
    --cores 2 --memory 2048 --swap 512 \
    --rootfs local-lvm:8 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --unprivileged 1 \
    --features nesting=1 \
    --onboot 1
```

`nesting=1` is needed for systemd to behave inside an unprivileged
container; `pct create` warns about it otherwise. Add it afterwards with
`pct set 110 --features nesting=1` if you already created the container.

Give it a static address (or a DHCP reservation) — workers do not need to
reach the Hub, but you and the CLI do.

## 2. Attach the shared storage

What matters is that every machine sees one storage at **one absolute
path**. How it gets there differs.

With a real NAS, mount it on the Proxmox host and bind-mount it into the
container — mounting NFS or CIFS inside an unprivileged container is
blocked, and working around that costs more than it saves. Add the share
to the host's `/etc/fstab`:

```
nas:/export/pisco  /mnt/pisco  nfs  defaults,_netdev  0 0
```

```bash
mkdir -p /mnt/pisco && mount /mnt/pisco
pct set 110 -mp0 /mnt/pisco,mp=/mnt/pisco
```

For a single-host trial there is no NAS to mount: use a host directory and
give every container the same mount point.

```bash
mkdir -p /srv/pisco
chown 101000:101000 /srv/pisco     # uid 1000 inside an unprivileged container
pct set 110 -mp0 /srv/pisco,mp=/mnt/pisco
```

**The mount point inside the container must be the same path the workers
use.** The Hub hands absolute paths to tools verbatim — that is what makes
remote execution need no file copying, and it is also what breaks if
`/mnt/pisco` on the Hub is `/data/pisco` on a worker.

One wrinkle with unprivileged containers: UIDs are shifted by 100000, so
container uid 1000 writes as host uid 101000. Containers on the same host
share that mapping and therefore agree with each other for free. A
separate machine mounting the same share over NFS does not — it sees
101000 and makes nothing of it.

So before there is data on the share, decide how identity works across
machines: run the tools everywhere as one dedicated user whose UID matches
the shifted one, or export with `all_squash` mapping everything to a
single owner. Otherwise a worker fails to read what the Hub wrote, and it
looks like a tool bug rather than a permissions one.

## 3. Install

Inside the container. The Hub runs from a plain virtualenv — nix is the
development environment, not a deployment requirement, and installing nix
in an unprivileged container is its own adventure.

```bash
apt update
apt install -y python3 python3-venv git openssh-client

adduser --system --group --home /opt/pisco-hub pisco
git clone -b toolbox-framework \
    https://git.geomar.de/team-optics/plankton/PIScO_Hub.git /opt/pisco-hub/app
cd /opt/pisco-hub/app

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The branch matters until this work is merged: `main` predates the tool
registry, the API and the CLI, and has no `requirements.txt` at all.
Cloning over HTTPS avoids putting a deploy key on the container; switch to
SSH if you would rather pull without a password.

No `python3-tk`, and no headed OpenCV: `requirements.txt` pins
`opencv-python-headless` because the GUI build needs libGL, which a
container has no reason to carry.

## 4. Configure

```bash
.venv/bin/python scripts/setup_env.py          # writes .env (secret key, DB url)
.venv/bin/python scripts/create_user.py        # your web login
.venv/bin/python scripts/create_api_token.py <user> cli full
```

Keep that token for the CLI. Then point the Hub's output root at the share,
either in Settings in the web UI or directly:

```bash
.venv/bin/python - <<'EOF'
from app.factory import create_app
from app.backend.models import GlobalSetting, db
app = create_app(create_worker=False)
with app.app_context():
    GlobalSetting.query.get("DATA_ROOT").value = "/mnt/pisco/hub-data"
    GlobalSetting.query.get("FILE_EXPLORER_ROOT").value = "/mnt/pisco"
    db.session.commit()
EOF
```

`DATA_ROOT` is where the Hub allocates task outputs. On a single machine
anywhere works; the moment a worker joins, it must be on the share.

## 5. SSH key for reaching workers

```bash
sudo -u pisco ssh-keygen -t ed25519 -N '' -f /opt/pisco-hub/.ssh/id_ed25519
cat /opt/pisco-hub/.ssh/id_ed25519.pub
```

That public key goes into the `authorized_keys` of the account the tools
run as on each worker. The Hub only ever uses non-interactive SSH, so the
key must have no passphrase and the host key must be accepted — the first
connection does that automatically (`StrictHostKeyChecking=accept-new`).

## 6. Run it as a service

`/etc/systemd/system/pisco-hub.service`:

```ini
[Unit]
Description=PIScO Hub
After=network-online.target remote-fs.target
Wants=network-online.target

[Service]
Type=simple
User=pisco
WorkingDirectory=/opt/pisco-hub/app
# The scheduler reports through plain prints, which would otherwise sit in
# a buffer instead of reaching the journal.
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/pisco-hub/app/.venv/bin/gunicorn \
    --workers 1 --threads 8 --timeout 120 \
    --bind 0.0.0.0:5000 main:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now pisco-hub
journalctl -u pisco-hub -f
```

**`--workers 1` is not a suggestion.** The scheduler, the process watcher
and the machine monitor are threads started when the app is created, so
every additional worker process is another scheduler dispatching the same
queue — tasks would be started more than once. Scale with `--threads`,
which shares one process, never with `--workers`.

Do not run `main.py` directly for anything but development, and never with
`HUB_DEBUG=1` on a real instance: the reloader restarts the process, and
the scheduler threads restart with it, underneath whatever is running.

## 7. Check it

```bash
curl -s http://<hub-ip>:5000/api/v1/health
```

Then from your workstation, against that address:

```bash
export PISCO_HUB_URL=http://<hub-ip>:5000
export PISCO_HUB_TOKEN=<the token from step 4>
hub machines list      # just 'local' for now
hub tools list         # empty until you register a toolbox
```

Register the toolboxes by the path they have **on the share**, so the same
manifest resolves identically from every machine:

```bash
hub providers add /mnt/pisco/tools/pisco_segmenter_cpp
```

## What comes next

Adding a worker is: install the tools and their dependencies on it, mount
the same share at the same path, authorise the Hub's SSH key, then
`hub machines add <name> --host <ip> --user <user> --tag gpu`. The Hub
probes it, and from then on the scheduler will place work there whenever a
tool's requirements and weight fit. Nothing about the tools or workflows
changes.
