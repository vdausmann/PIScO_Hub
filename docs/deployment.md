# Deploying the Hub and its workers

The Hub is a small Flask server plus three background threads. It needs no
GPU and very little CPU — it schedules work, it does not do the work — so
a modest container is enough. What it *does* need is to see the shared
storage at the same paths its workers see, and to reach those workers over
SSH.

Part one sets up the Hub. Part two adds a machine it can dispatch to.

## Sizing

2 cores, 2 GB RAM, 8 GB disk is comfortable. The database is SQLite and
stays small, and logs live in the container. Each task's settings file and
its results go to shared storage, in one directory per task, because a
task may run on another machine and has to be able to read them.

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
pct set 110 -mp0 /srv/pisco,mp=/mnt/pisco
```

Then give it to the service user *from inside the container*, once the
mount is there. Do not try to work out the host-side UID: `adduser
--system` allocates from the system range rather than the 1000 you might
expect, and container root can chown the mount anyway because the owning
UID falls inside its mapped range.

```bash
# in the container, after step 3 has created the user
chown -R pisco:pisco /mnt/pisco
```

**The mount point inside the container must be the same path the workers
use.** The Hub hands absolute paths to tools verbatim — that is what makes
remote execution need no file copying, and it is also what breaks if
`/mnt/pisco` on the Hub is `/data/pisco` on a worker.

One wrinkle with unprivileged containers: UIDs are shifted by 100000, so
container uid 1000 writes as host uid 101000. Containers on the same host
share that *offset*, but that only helps if the accounts inside them have
the same UID — and `adduser --system` takes the next number free in each
container, which will not match. Create the worker's account with the
Hub's UID explicitly (`id -u pisco` on the Hub, then `adduser --uid`), or
the worker cannot write what the Hub created. A separate machine mounting
the share over NFS has the same problem and cannot solve it this way: it
sees 101000 and makes nothing of it.

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
install -d -o pisco -g pisco /opt/pisco-hub/.ssh
runuser -u pisco -- ssh-keygen -t ed25519 -N '' -f /opt/pisco-hub/.ssh/id_ed25519
cat /opt/pisco-hub/.ssh/id_ed25519.pub
```

A fresh container has no credentials, so cloning fails with "Permission
denied (publickey)". Add that public key to the project in GitLab under
Settings → Repository → Deploy keys, with read access. The same key is
what reaches the workers later, so it earns its keep twice. Cloning over
HTTPS works too, but a private project will then ask for a personal
access token on every fetch.

```bash
runuser -u pisco -- git clone -b toolbox-framework \
    git@git.geomar.de:team-optics/plankton/PIScO_Hub.git /opt/pisco-hub/app
cd /opt/pisco-hub/app

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The branch matters until this work is merged: `main` predates the tool
registry, the API and the CLI, and has no `requirements.txt` at all.

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

Those scripts create the database, so if you ran them as root the service
will not be able to write it. Hand everything back before starting the
service:

```bash
chown -R pisco:pisco /opt/pisco-hub
```

The Hub runs `create_all` and its migrations on every startup, so this is
a standing requirement rather than a first-run one. Getting it wrong looks
like `sqlite3.OperationalError: attempt to write a readonly database` in
the journal, and note that SQLite needs to write the *directory* too — WAL
mode puts `-wal` and `-shm` files next to the database.

## 5. SSH key for reaching workers

The key generated in step 3 is the one the Hub uses:

```bash
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

# Doing this more than once

Registering a key per container gets old quickly, so avoid needing to.

A worker never clones this repository — it runs tools, not the Hub.

For the tool repositories, note what the Hub actually requires: it reads
`toolbox.json` and any settings templates, and passes `program_path`
through to the worker without ever looking at it. So what must match
across machines is the **path**, not the storage behind it. Both of these
work:

```bash
# same path on every machine, each with its own copy
git clone <tool repo> /opt/pisco/tools/pisco_segmenter_cpp

# or one copy on the share, mounted at that path everywhere
git clone <tool repo> /srv/pisco/tools/pisco_segmenter_cpp
```

Prefer local copies. A tool's runtime — nix, conda, drivers — is
per-machine regardless, so sharing the code saves a clone rather than any
real work; a build directory inside the repo means two machines can
collide building or entering it; and running a binary across a VPN-mounted
share, as a remote worker would, is slow and fragile in a way that data
access is not.

The cost is that copies drift. Nothing stops one worker running an older
tool than another, giving different results for the same workflow
depending on where it ran, and the Hub cannot see it — it trusts the path.
Bump `version` in the manifest when a tool changes and check
`hub tools describe <tool>` against what you believe is deployed. That is
a convention, not an enforcement.

For repeat container builds, make this one a template once it works
(`pct stop 110 && pct template 110`, then `pct clone`). Clones inherit the
venv and the key, which also means they share one identity; fine for a
read-only key, worth knowing before it is anything more.

If you would rather not exchange keys at all, a GitLab **deploy token**
(Settings → Repository → Deploy tokens, `read_repository`) is a
username/password usable over HTTPS from any number of machines and
revocable in one place. The cost is a secret in the clone URL.

# Part two: adding a worker

A worker is any machine the Hub can SSH into. It runs no Hub code and
holds no state — the Hub writes a settings file, runs one command, and
reads the exit code. Concretely it needs four things:

1. **SSH access** for the Hub's key, non-interactive.
2. **The shared storage at the same path** as on the Hub, writable by the
   account the Hub logs in as. Each task's settings file and its outputs
   live there; this is how the worker receives its input and hands back
   its results.
3. **The tools at the same paths** the manifest resolved to on the Hub.
4. **Whatever those tools need to run** — nix, conda, drivers. This is
   per-machine and is the bulk of the work.

## Keep tools out of the Hub's checkout

Before adding a worker, move any toolbox that lives inside the Hub's
directory. A provider registered at `/opt/pisco-hub/app/examples/...`
would force every worker to have the Hub's checkout at that path. Give
toolboxes a home of their own, identical on every machine:

```bash
mkdir -p /opt/pisco/tools
cp -r /opt/pisco-hub/app/examples/chain_demo /opt/pisco/tools/
chown -R pisco:pisco /opt/pisco
```

Register the new location and drop the old provider (`hub providers list`
for its id, then `hub providers remove <id>`). Tools keep their identity
across the move — the registry re-points them rather than replacing them,
so existing workflows still resolve.

## Prepare the machine

For a second container on the same host, create it exactly like the Hub
and give it the same mount:

```bash
pct create 111 local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst \
    --hostname pisco-worker1 \
    --cores 4 --memory 4096 --swap 512 \
    --rootfs local-lvm:16 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --unprivileged 1 --features nesting=1 --onboot 1
pct set 111 -mp0 /srv/pisco,mp=/mnt/pisco
pct start 111
```

Inside it, create the account the Hub will log in as, authorise the Hub's
key, and give it the share:

Use the Hub's UID for the account (`id -u pisco` there), so both machines
agree about who owns what on the share:

```bash
apt update && apt install -y openssh-server python3

adduser --system --group --uid <hub's uid> --home /home/pisco --shell /bin/bash pisco
install -d -o pisco -g pisco -m 700 /home/pisco/.ssh
# paste the Hub's /opt/pisco-hub/.ssh/id_ed25519.pub here:
editor /home/pisco/.ssh/authorized_keys
chown pisco:pisco /home/pisco/.ssh/authorized_keys
chmod 600 /home/pisco/.ssh/authorized_keys

chown -R pisco:pisco /mnt/pisco
```

Note the `--shell /bin/bash`: the Hub runs its command through the login
shell, and the `nologin` shell that `adduser --system` defaults to would
refuse it.

Then place the tools at the same path the Hub uses, and install their
runtimes. For the demo toolbox that is a copy and nothing else; for the
segmenter it is nix plus a built `bin/Segmenter`.

## Check the hop by hand first

From the Hub container, as the Hub's user — this is exactly what the Hub
will do, so if it fails here it will fail there:

```bash
runuser -u pisco -- ssh -o BatchMode=yes pisco@<worker-ip> \
    'echo ok; ls -d /mnt/pisco /opt/pisco/tools/chain_demo'
```

You want `ok` and both paths listed. `Permission denied` means the key or
its file modes; a missing path means the mount or the tool copy.

## Register it

```bash
hub machines add worker1 --host <worker-ip> --user pisco
hub machines list
```

The Hub probes on registration, so the status tells you immediately
whether it can reach the machine. Add `--tag gpu` on machines that have
one, and `--max-weight N` to say how much work the machine may run at
once.

Then send it something deliberately, rather than hoping the scheduler
picks it:

```bash
hub workflow create --name remote-test \
    --task tool=demo_produce machine=worker1 settings='{"label":"remote"}' \
    --watch
hub workflow status <id>      # the MACHINE column should read worker1
```

## When a remote task fails

`hub task logs <id> --stderr` carries the remote program's own output; ssh
forwards it. Beyond that:

| Symptom | Cause |
|---|---|
| Machine shows `offline` | Hub cannot ssh in: key, host, or firewall |
| Exit code 255, no output | ssh itself failed — usually the host key or a refused connection |
| "No such file or directory" naming the settings file | The share is not mounted on the worker, or not at the same path |
| "No such file or directory" naming the program | The toolbox is missing on the worker, or at a different path |
| Permission denied writing results | The login account cannot write the share; check the UID discussion above |
| Task hangs as Running forever | The remote program is waiting on something — the Hub cannot cancel it, so kill it over ssh |

A remote task that is killed leaves the remote program running: the Hub
only controls its local ssh process. There is no cancel operation yet.
