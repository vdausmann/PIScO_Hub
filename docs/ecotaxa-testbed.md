# EcoTaxa on the testbed

A procedure for standing an EcoTaxa server up as a container on the pve
testbed, with a development environment beside it. Every step here was
executed and verified on **CT 114 `ecotaxa`, 10.11.11.114**.

EcoTaxa is not a PIScO tool and the Hub does not drive it. It is here as
the destination for classified objects, and because its ML backend is a
plausible home for our own prediction routine — see [The ML seam](#the-ml-seam).

## What this establishes

- The `all_in_one` docker stack runs unmodified-in-spirit inside an
  unprivileged LXC, and survives a container reboot unattended.
- A second, source-based environment (Python 3.14 venvs) runs the same
  back and front with hot reload, against the same database and vault.
- A P4 can be **shared** into the container without taking it from an
  existing GPU worker.
- The ML backend can run from a working copy of its source with no image
  rebuild, which is what makes wiring in our own routine practical.

Sizing that worked: 4 cores / 8 GB / 64 GB. The images alone are ~10 GB
(the GPU backend is 2.9 GB compressed), and the venvs and node modules
add a couple more.

## Decide these first

- **EcoTaxa owns its vault.** `/opt/ecotaxa/vault` is EcoTaxa's managed
  image store and grows with every import. It is not the PIScO share and
  should not be pointed at it.
- **The share is an input, not a workspace.** `/mnt/pisco` is mounted as a
  *read-only subfolder* of EcoTaxa's server-load area, so imports can read
  what the Hub wrote and EcoTaxa can never write back. See the traps below
  for why it cannot simply be the load area itself.
- **One database.** The stack and the dev servers share it. That is
  deliberate — you develop against real data — but it means only one
  backend may run at a time (see `devctl`).

## 1. The container

```bash
pct create 114 local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst \
    --hostname ecotaxa \
    --cores 4 --memory 8192 --swap 2048 --rootfs local-lvm:64 \
    --net0 name=eth0,bridge=vmbr0,ip=10.11.11.114/24,gw=10.11.11.1 \
    --nameserver 10.11.11.1 --searchdomain sea-through.de \
    --unprivileged 1 --features nesting=1,keyctl=1 \
    --onboot 1 --start 1
pct set 114 -mp0 /srv/pisco,mp=/mnt/pisco
pct reboot 114
```

`nesting=1,keyctl=1` is what Docker needs; without it the daemon starts
but containers fail. Fix the locale while you are in there, or every
`apt` run is buried in perl warnings:

```bash
sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
locale-gen && update-locale LANG=en_US.UTF-8
```

## 2. Docker

Docker's own repository, not Debian's:

```bash
apt-get install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker run --rm hello-world      # the real test of nesting
```

Verified: Docker 29.8 on `overlayfs`, cgroup v2.

## 3. The stack

The compose stack lives in the *front* repo. Take a working copy so
pulling upstream never collides with local edits:

```bash
cd /opt && git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/ecotaxa/ecotaxa_front.git
cd ecotaxa_front && git sparse-checkout set docker/all_in_one
cp -r docker/all_in_one /opt/ecotaxa
cd /opt/ecotaxa && mkdir -p vault models ftp_area file_srv/pisco eco_users_files
```

Then edit `back_config.ini` — these are not optional, see the traps:

```ini
SERVERURL     = http://10.11.11.114:8088
RO_DB_USER    = postgres          # mandatory in current back, ships commented out
RO_DB_PASSWORD = mysecretpassword
RO_DB_HOST    = pgdb
RO_DB_PORT    = 5432
RO_DB_DATABASE = ecotaxa
```

and `docker-compose.yml`:

```yaml
    # the share, read-only, INSIDE a writable load area
    - ./file_srv:/file_srv
    - /mnt/pisco:/file_srv/pisco:ro
    # postgres on loopback only, not the whole 10.11.11.0/24
    - 127.0.0.1:5432:5432
```

Give every service `restart: unless-stopped` — upstream sets it only on
the GPU backend, so without this nothing returns after a reboot.

Install, staying detached (upstream's `install_all_in_one.sh` ends in a
foreground `docker compose up`, which is not what you want on a server):

```bash
docker compose -p portable up -d
docker exec -i portable_pgdb_1 psql -U postgres -c \
  "create DATABASE ecotaxa WITH OWNER=postgres ENCODING='UTF8' \
   TEMPLATE=template0 LC_CTYPE='C' LC_COLLATE='C' CONNECTION LIMIT=-1;"
docker exec -i portable_ecotaxaback bash -c \
  "PYTHONPATH=. python cmds/manage.py db build"          # 34 tables
docker exec -i portable_pgdb_1 psql -U postgres -d ecotaxa -c \
  "UPDATE users SET email='administrator@mail.test', password='Ecotaxa?1' WHERE id=1;"
```

UI on **http://10.11.11.114:8088**, login `administrator@mail.test` /
`Ecotaxa?1`. Testbed credentials; the crypto keys are upstream defaults.
Note `SECRET_KEY` cannot be changed after `db build` without invalidating
every stored password.

**One-time taxonomy sync.** Imports will not work until the tree is
pulled from EcoTaxoServer. Either click the warning popup in the UI, or:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8088/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"administrator@mail.test","password":"Ecotaxa?1"}' | tr -d '"')
curl -s "http://127.0.0.1:8088/api/taxa/pull_from_central" \
  -H "Authorization: Bearer $TOKEN"
```

This pulled **12,044 taxa**; a second call returning
`{"inserts":0,"updates":0,"error":null}` is how you confirm it finished.
It takes minutes, and through nginx it will **return 504 long before it
is done** — the backend carries on regardless, so the timeout is
cosmetic, but see the traps for the fix and prefer calling the backend
directly. The shipped `TAXOSERVER_INSTANCE_ID`/`SHARED_SECRET` are the
public all_in_one defaults, shared by every portable instance.

## 4. GPU — sharing, not taking

LXC passthrough is only device nodes, so the same P4 can be handed to
more than one container. CT 114 shares `/dev/nvidia1` with CT 113 and
neither loses anything. Device block per
[cluster-runbook](cluster-runbook.md); driver userspace must be the exact
host version with the kernel module skipped:

```bash
pct set 114 -dev0 /dev/nvidia1,gid=44 -dev1 /dev/nvidiactl,gid=44 \
  -dev2 /dev/nvidia-modeset,gid=44 -dev3 /dev/nvidia-uvm,gid=44 \
  -dev4 /dev/nvidia-uvm-tools,gid=44 \
  -dev5 /dev/nvidia-caps/nvidia-cap1,gid=44 \
  -dev6 /dev/nvidia-caps/nvidia-cap2,gid=44
pct reboot 114
# inside:
/root/NVIDIA-Linux-x86_64-580.178.04.run --silent --no-kernel-module
```

Docker additionally needs the NVIDIA container toolkit, and **`no-cgroups`
must be turned on** or `--gpus all` fails in an unprivileged container:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update && apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
sed -i 's/^#\?no-cgroups = false/no-cgroups = true/' \
  /etc/nvidia-container-runtime/config.toml
systemctl restart docker
docker run --rm --gpus all ubuntu:22.04 nvidia-smi -L
```

Then reserve a GPU for the ML service in `docker-compose.yml`:

```yaml
    deploy:
      resources:
        reservations:
          devices:
            - {driver: nvidia, count: 1, capabilities: [gpu]}
```

## 5. The development environment

The stack runs pre-built images, so its Python cannot be edited. For that
you need source checkouts. The back and front require **Python 3.14**,
which Debian 13 does not ship — `uv` is the least painful way in:

```bash
apt-get install -y build-essential postgresql-client nodejs npm
curl -LsSf https://astral.sh/uv/install.sh | sh
ln -sf /root/.local/bin/uv /usr/local/bin/uv
uv python install 3.14

mkdir -p /opt/dev && cd /opt/dev
for r in ecotaxa_back ecotaxa_front ecotaxa_ML_back; do
  git clone https://github.com/ecotaxa/$r.git
done
cd /opt/dev/ecotaxa_back/py && uv venv --python 3.14 .venv
VIRTUAL_ENV=$PWD/.venv uv pip install -r requirements.txt
cd /opt/dev/ecotaxa_front && uv venv --python 3.14 .venv
VIRTUAL_ENV=$PWD/.venv uv pip install -r requirements.txt
```

Everything installs as wheels; no build dependencies are needed despite
`psycopg`, `Pillow` and `lxml` being in the list.

The dev backend's `config.ini` is the stack's `back_config.ini` with
`DB_HOST`/`RO_DB_HOST` set to `localhost` (postgres is published on
loopback) and the directories pointed at the stack's, so both see the
same vault and models. The dev frontend's `config/config.cfg` must reuse
the backend's `SECRET_KEY` and mail keys or tokens will not validate.

Launchers are `/opt/dev/run_back_dev.sh` (uvicorn, `--reload`, :8001) and
`/opt/dev/run_front_dev.sh` (Flask debug, :5001, plain HTTP — upstream's
`runserver.py` insists on mkcert certificates we do not have).

## 6. Switching modes — `devctl`

Three things can run here and some of them collide: two backends against
one database both run the job scheduler, and two ML workers both claim
`Prediction` jobs. `/usr/local/bin/devctl` sequences it:

```bash
devctl status            # containers, dev processes, which endpoints answer
devctl stack up|down     # the packaged stack           :8088
devctl dev   up|down     # source back+front, hot reload :8001 / :5001
devctl ml    up|down     # ML worker from your source tree
```

`devctl dev up` stops the stack's back and front and leaves postgres and
nginx running; `devctl dev down` puts them back. Under `pct exec`, call
it by full path — that PATH has no `/usr/local/bin`.

## The ML seam

`ecotaxa_ML_back` is where a custom prediction routine goes. It exposes
**no REST API**: `ml_jobs_runner.py` polls the shared postgres `job` table
for `Prediction` jobs, runs them, and writes results back. The main
backend deliberately excludes that job type
(`JobScheduler.FILTER = [PredictForProject.JOB_TYPE]`), so the two never
compete.

The pipeline is CNN deep-feature extraction → PCA → Random Forest, in
**TensorFlow** — worth noting, since our own tools are PyTorch.

There are two levels of integration, and the cheaper one may be enough:

1. **No fork.** Training the CNN feature extractor happens *outside*
   EcoTaxa by design. A model dropped into `MODELSAREA` (`/models`) is
   selectable per project.
2. **Fork the repo.** `/opt/dev/ml-dev.yml` runs
   `ecotaxa/ecotaxa_ml_back:v1.0` with `/opt/dev/ecotaxa_ML_back/src`
   bind-mounted over `/app` and the command overridden to
   `python3 ml_jobs_runner.py`. Edit the working copy, `devctl ml restart`,
   no image rebuild.

**Mind the image versions.** Published `ecotaxa/ecotaxa_gpu_back:latest`
is a 2026-01 build still running `gpu_jobs_runner.py`, from before the ML
code was split out of `ecotaxa_back`. `ecotaxa/ecotaxa_ml_back:v1.0`
(2026-06) is the one whose `/app` matches the `ecotaxa_ML_back` repo, so
it is the only sane base for mounting a working copy over.

## Traps that cost us time

- **The all_in_one config is stale against the current backend.**
  `RO_DB_*` is mandatory but ships commented out; the back exits 3 with
  `Mandatory configuration key 'RO_DB_HOST' is missing`. There is no
  read-only replica here, so point it at the same database.
- **`SERVERLOADAREA` must be writable**, despite the shipped comment
  saying "ro by the back-end". `AppConfig.validate()` asserts it, and a
  read-only mount fails startup with `Directory '/file_srv' is not
  writable`. Hence the share going in as a read-only *subfolder* of a
  writable local directory — which is the better arrangement anyway.
- **nginx resolves its upstreams once, at startup.** Any
  `docker compose up -d` that recreates `ecotaxaback` or `ecotaxafront`
  gives it a new IP and every `/api/` call then returns 502 while the
  frontend still serves 200. Restart nginx after a recreate; `devctl`
  does. A stack-wide restart is fine because everything re-resolves.
- **nginx 504s any long API call.** The shipped config sets
  `uwsgi_read_timeout 600` for the frontend but nothing for `/api/`, so
  the proxy default of 60s applies. The taxonomy pull dies at exactly one
  minute while the backend keeps working — alarming and misleading. Add
  `proxy_read_timeout 900` (and `proxy_send_timeout`) to the `/api/`
  location. Done here.
- **The GPU backend crash-looping on `relation "job" does not exist` is
  normal** before `db build`, and stops on its own afterwards.
- `pct exec` has no `/usr/local/bin` in PATH, and `pkill -f <pattern>`
  matches the very `bash -c` running it. Both are in
  [cluster-runbook](cluster-runbook.md); both bit again here.

## Still outstanding

- ~~Nothing imports from `/mnt/pisco` yet.~~ **Done.** A four-task
  workflow (segment → deconvolve → extract → `pisco_ecotaxa_export`)
  produced an archive that imported into EcoTaxa with images, metadata
  and morphometrics. The exporter lives in `process_pisco_profiles`
  (branch `ecotaxa-export-from-hdf5`, unpushed); see its
  `README_ECOTAXA.md`. Staging area for import archives is
  `/srv/pisco/ecotaxa-imports`, which EcoTaxa sees at
  `/file_srv/pisco/ecotaxa-imports`.
- **PIScO is not a registered EcoTaxa instrument.** Project creation
  fails with a foreign-key violation unless the instrument exists;
  the test projects use `Other camera`. Registering PIScO properly on
  the production instance is outstanding.
- The ML fork is scaffolded, not written. The TensorFlow/PyTorch split is
  the real question: whether to port a routine into their pipeline or run
  ours as a separate worker against the same `job` table.
- Credentials and crypto keys are upstream defaults throughout.
- No backups. The vault and the database are both on the container's
  local-lvm disk.
