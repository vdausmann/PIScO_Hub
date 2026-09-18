# GitLab cruise mirror

A self-hosted GitLab CE that carries the Team Optics code (git.geomar.de)
onto a cruise, and brings the cruise's work back afterwards.

## Where it runs

**CT 115 `gitlab`, http://10.11.11.115** on the pvetemp Proxmox host.
Debian 13, unprivileged, `nesting=1`, 4 cores / 8 GB / 128 GB, `onboot 1`.
GitLab CE omnibus installed from packages.gitlab.com (script:
`tools/gitlab-mirror/install-gitlab.sh`); config in `/etc/gitlab/gitlab.rb`,
`gitlab-ctl status|reconfigure|tail` as usual. Two things the installer
must do in an unprivileged LXC, or `gitlab-ctl reconfigure` fails:
`package['modify_kernel_parameters'] = false` (the container cannot
`sysctl kernel.shmmax`) and a UTF-8 locale (`LANG=C.UTF-8` in
`/etc/default/locale`; the Debian template ships none and `initdb` refuses).
Both are in the script. `external_url` is the plain
IP, so the container must keep 10.11.11.115 wherever it is restored —
or edit `external_url` in `gitlab.rb` and `gitlab-ctl reconfigure`.

The root password from the install is in `/etc/gitlab/initial_root_password`
inside the container (GitLab deletes it after 24 h; change it on first
login and put the new one in the team password store).

## Taking it on the cruise

The container *is* the deliverable:

    vzdump 115 --mode snapshot --compress zstd --storage local
    # → /var/lib/vz/dump/vzdump-lxc-115-*.tar.zst, restore with
    pct restore 115 <file> --storage local-lvm

Restore on whichever Proxmox host travels, keep the IP (or fix
`external_url`), `pct start 115`.

## Mirroring Team Optics

Tooling: `tools/gitlab-mirror/gitlab-mirror.py`, deployed at
`/root/gitlab-mirror.py` in CT 115, config in `/root/.gitlab-mirror.env`
(see `gitlab-mirror.env.example`; mode 0600, tokens never leave the
container). Run it inside the container:

    pct exec 115 -- /root/gitlab-mirror.py list             # sanity check the group tree
    pct exec 115 -- /root/gitlab-mirror.py direct-transfer  # repos + issues + MRs + wiki
    pct exec 115 -- /root/gitlab-mirror.py repos            # git-level mirror, re-runnable
    pct exec 115 -- /root/gitlab-mirror.py push-back        # after the cruise

Two mechanisms, because GitLab CE has no pull mirroring (Premium only):

1. **`direct-transfer`** — GitLab's own group migration. Carries issues,
   merge requests, labels, milestones, wikis, members (if users exist on
   both sides). Needs *"Allow migrating groups and projects by direct
   transfer"* enabled on **git.geomar.de** (the script enables it on our
   side). If GEOMAR RZ has it off, the call fails immediately with a clear
   message — ask them, or settle for `repos`. The source token needs **`api`**
   scope (`read_api` is rejected with a 422 "check that the source instance
   base URL and the personal access token meet the necessary requirements").
   One-shot, and it wants a destination group that does not exist yet: if
   `repos` already ran, delete `team-optics` on the cruise instance first
   (Group → Settings → General → Advanced → Delete) or set a different
   `DST_GROUP`. Then keep code in sync with `repos`.
2. **`repos`** — plain git: `clone --mirror` each project (all branches,
   tags, LFS) into `/srv/gitlab-mirror/`, create the group/project on our
   instance if missing, `push --mirror`. Idempotent; run it the day before
   departure. Source token needs `read_api` + `read_repository`.

`push-back` reverses `repos`: clones from the cruise instance and pushes
branches and tags to git.geomar.de **without force**. Anything that moved
on both sides is listed as DIVERGED and left alone for a manual merge.
It never deletes a branch on GEOMAR.

Git and git-lfs authenticate through the script itself acting as a git
credential helper (`gitlab-mirror.py cred`), so tokens never land in a
`.git/config` or a process listing. Do **not** switch this to
`http.extraheader`: GitLab's LFS batch responses carry their own
`Authorization` header per object, and a second one makes every LFS
download fail with "Client error". LFS objects are pushed *before* the
refs, because the destination's pre-receive hook rejects commits whose
LFS pointers have no object yet.

## State (2026-09-14)

- CT 115 built, GitLab CE 19.3.2 (git.geomar.de runs 19.3.2-ee, same
  release). Root token `gitlab-mirror` (api, 1 year) created for the script.
- Source token: a **group access token** on `team-optics`, role
  **Developer**, scopes `read_api` + `read_repository` + `write_repository`,
  **expires 2026-10-14**. Sufficient for `repos` and `push-back`; not for
  `direct-transfer` (no `api` scope). Renew before it lapses. The first
  attempt used a Guest-role token: the API listing works with Guest, but
  every clone is refused with 403 "not allowed to download code".
- `team-optics` has 26 projects in 5 subgroups (bioluminescence, lightfield,
  pisco-v4-hardwarecode, plankton, propel). All 26 are mirrored incl. LFS
  (morphocluster, swimming_in_salt); mirror cache `/srv/gitlab-mirror`,
  ~800 MB. A full re-run takes a couple of minutes.

## Known limits

- Users: mirrored projects are owned by the root user on the cruise
  instance; create the team's accounts there by hand (Admin → Users) or
  let direct-transfer map them by email.
- CI: `.gitlab-ci.yml` pipelines will try to run on the cruise instance
  and fail for lack of runners — harmless, or disable CI/CD per project.
- Nothing on the cruise instance pushes to GEOMAR automatically; the
  merge back is the deliberate `push-back` step.
