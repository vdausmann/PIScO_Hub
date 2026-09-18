#!/usr/bin/env python3
"""Mirror a GitLab group tree between two instances.

Config comes from an env file (default /root/.gitlab-mirror.env, mode 0600):

    SRC_URL=https://git.geomar.de
    SRC_TOKEN=glpat-...          # read_api + read_repository (api for direct-transfer)
    SRC_GROUP=team-optics        # full path of the group to mirror
    DST_URL=http://10.11.11.115
    DST_TOKEN=glpat-...          # api scope, admin user
    DST_GROUP=team-optics        # full path on the destination (created if missing)

Subcommands:
    list             projects under SRC_GROUP (incl. subgroups)
    direct-transfer  GitLab "direct transfer" migration: repos + issues + MRs + wiki.
                     Needs bulk_import enabled on BOTH instances; the script enables
                     it on DST, SRC is up to its admins.
    repos            git-level sync SRC -> DST: creates groups/projects, mirrors all
                     branches + tags (+ LFS if git-lfs is present). Re-runnable.
    push-back        reverse: fetch every project from DST and push branches/tags to
                     SRC *without* force. Diverged branches are reported, not clobbered.

    cred             internal: git credential helper (the script points git at itself)

Stdlib only so it runs on the bare container.
"""
import argparse, json, os, shlex, subprocess, sys, time, urllib.parse, urllib.request

WORK = "/srv/gitlab-mirror"


def load_env(path):
    cfg = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("SRC_URL", "SRC_TOKEN", "SRC_GROUP", "DST_URL", "DST_TOKEN", "DST_GROUP"):
        if k not in cfg:
            sys.exit(f"{path}: missing {k}")
    cfg["SRC_URL"] = cfg["SRC_URL"].rstrip("/")
    cfg["DST_URL"] = cfg["DST_URL"].rstrip("/")
    return cfg


class Api:
    def __init__(self, base, token):
        self.base, self.token = base, token

    def call(self, method, path, data=None, params=None, ok404=False):
        url = f"{self.base}/api/v4{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("PRIVATE-TOKEN", self.token)
        if body:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = r.read().decode()
                return (json.loads(txt) if txt else None), r.headers
        except urllib.error.HTTPError as e:
            if e.code == 404 and ok404:
                return None, {}
            sys.exit(f"{method} {url} -> {e.code}: {e.read().decode()[:500]}")

    def get(self, path, **params):
        return self.call("GET", path, params=params)[0]

    def get_all(self, path, **params):
        params = dict(params, per_page=100, page=1)
        out = []
        while True:
            items, hdr = self.call("GET", path, params=params)
            out.extend(items)
            nxt = hdr.get("X-Next-Page")
            if not nxt:
                return out
            params["page"] = int(nxt)

    def post(self, path, data):
        return self.call("POST", path, data=data)[0]

    def put(self, path, data):
        return self.call("PUT", path, data=data)[0]

    def find(self, path):
        return self.call("GET", path, ok404=True)[0]


def enc(p):
    return urllib.parse.quote(p, safe="")


# ---------------------------------------------------------------- git helpers
SELF = os.path.abspath(__file__)
ENV_PATH = "/root/.gitlab-mirror.env"


def cmd_cred(cfg, _):
    """git credential helper protocol: read key=value lines, answer with the
    token for that host. Used for git and git-lfs alike, so tokens never land
    in .git/config or in a process listing. (http.extraheader breaks git-lfs:
    the LFS batch response carries its own Authorization header.)"""
    req = dict(l.split("=", 1) for l in sys.stdin.read().splitlines() if "=" in l)
    for url_key, tok_key in (("SRC_URL", "SRC_TOKEN"), ("DST_URL", "DST_TOKEN")):
        u = urllib.parse.urlparse(cfg[url_key])
        if req.get("host") == u.netloc and req.get("protocol", u.scheme) == u.scheme:
            print("username=oauth2"); print(f"password={cfg[tok_key]}")
            return


def git(args, cwd=None):
    """Run git with the credential helper above (git-lfs honours it too)."""
    cmd = ["git", "-c", "credential.helper=", "-c", f"credential.helper={SELF} --env {ENV_PATH} cred",
           "-c", "lfs.locksverify=false"] + args
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, env=env)


def have_lfs():
    return subprocess.run(["git", "lfs", "version"], capture_output=True).returncode == 0


# ---------------------------------------------------------------- source tree
def src_projects(cfg):
    src = Api(cfg["SRC_URL"], cfg["SRC_TOKEN"])
    grp = src.get(f"/groups/{enc(cfg['SRC_GROUP'])}")
    projs = src.get_all(f"/groups/{grp['id']}/projects", include_subgroups="true", archived="false")
    projs += [p for p in src.get_all(f"/groups/{grp['id']}/projects", include_subgroups="true", archived="true")
              if p["id"] not in {q["id"] for q in projs}]
    return grp, sorted(projs, key=lambda p: p["path_with_namespace"])


def cmd_list(cfg, _):
    grp, projs = src_projects(cfg)
    print(f"{grp['full_path']}  ({grp['web_url']})  {len(projs)} projects")
    for p in projs:
        flags = []
        if p.get("archived"):
            flags.append("archived")
        if p.get("empty_repo"):
            flags.append("empty")
        stats = p.get("statistics") or {}
        print(f"  {p['path_with_namespace']:60s} {p['visibility']:9s} {' '.join(flags)}")


# ---------------------------------------------------------------- destination
def ensure_group(dst, full_path, src_group_meta=None):
    g = dst.find(f"/groups/{enc(full_path)}")
    if g:
        return g
    parent_path, _, leaf = full_path.rpartition("/")
    parent = ensure_group(dst, parent_path) if parent_path else None
    data = {"name": leaf, "path": leaf, "visibility": "private"}
    if parent:
        data["parent_id"] = parent["id"]
    if src_group_meta and src_group_meta.get("full_path") == full_path:
        data["name"] = src_group_meta["name"]
        data["description"] = src_group_meta.get("description") or ""
    print(f"  + group {full_path}")
    return dst.post("/groups", data)


def dst_path_for(cfg, src_path):
    assert src_path.startswith(cfg["SRC_GROUP"] + "/"), src_path
    return cfg["DST_GROUP"] + src_path[len(cfg["SRC_GROUP"]):]


def ensure_project(dst, cfg, sp):
    dpath = dst_path_for(cfg, sp["path_with_namespace"])
    p = dst.find(f"/projects/{enc(dpath)}")
    if p:
        return p
    ns_path, _, leaf = dpath.rpartition("/")
    ns = ensure_group(dst, ns_path)
    print(f"  + project {dpath}")
    return dst.post("/projects", {
        "name": sp["name"], "path": leaf, "namespace_id": ns["id"],
        "description": sp.get("description") or "", "visibility": "private",
        "lfs_enabled": True, "initialize_with_readme": False,
    })


# ---------------------------------------------------------------- repos sync
def cmd_repos(cfg, args):
    grp, projs = src_projects(cfg)
    dst = Api(cfg["DST_URL"], cfg["DST_TOKEN"])
    ensure_group(dst, cfg["DST_GROUP"], grp)
    lfs = have_lfs()
    os.makedirs(WORK, exist_ok=True)
    failed = []
    for sp in projs:
        if args.only and not any(o in sp["path_with_namespace"] for o in args.only):
            continue
        print(f"== {sp['path_with_namespace']}")
        dp = ensure_project(dst, cfg, sp)
        if sp.get("empty_repo"):
            print("   empty repo, skipped")
            continue
        d = os.path.join(WORK, sp["path_with_namespace"] + ".git")
        src_url = sp["http_url_to_repo"]
        if os.path.isdir(d):
            r = git(["remote", "update", "--prune"], cwd=d)
        else:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            r = git(["clone", "--mirror", src_url, d])
        if r.returncode:
            print("   FETCH FAILED:", r.stderr.strip()[-400:]); failed.append(sp["path_with_namespace"]); continue
        # LFS objects must reach the destination before the refs that point at
        # them, or its pre-receive hook rejects the push.
        if lfs and git(["lfs", "ls-files", "--all"], cwd=d).stdout.strip():
            r = git(["lfs", "fetch", "--all", src_url], cwd=d)
            if r.returncode:
                print("   LFS FETCH FAILED:", (r.stderr + r.stdout).strip()[-400:]); failed.append(sp["path_with_namespace"]); continue
            r = git(["lfs", "push", "--all", dp["http_url_to_repo"]], cwd=d)
            if r.returncode:
                print("   LFS PUSH FAILED:", (r.stderr + r.stdout).strip()[-400:]); failed.append(sp["path_with_namespace"]); continue
        r = git(["push", "--mirror", dp["http_url_to_repo"]], cwd=d)
        if r.returncode:
            print("   PUSH FAILED:", r.stderr.strip()[-600:]); failed.append(sp["path_with_namespace"]); continue
        # keep default branch, archived flag in step
        dst.put(f"/projects/{dp['id']}", {"default_branch": sp.get("default_branch") or "main"})
        if sp.get("archived") and not dp.get("archived"):
            dst.post(f"/projects/{dp['id']}/archive", {})
        print("   ok")
    if failed:
        print("\nFAILED:", *failed, sep="\n  "); sys.exit(1)
    print(f"\nall {len(projs)} projects mirrored")


# ---------------------------------------------------------------- push back
def cmd_push_back(cfg, args):
    grp, projs = src_projects(cfg)
    dst = Api(cfg["DST_URL"], cfg["DST_TOKEN"])
    back = os.path.join(WORK, "_pushback")
    os.makedirs(back, exist_ok=True)
    diverged, failed = [], []
    for sp in projs:
        if args.only and not any(o in sp["path_with_namespace"] for o in args.only):
            continue
        dpath = dst_path_for(cfg, sp["path_with_namespace"])
        dp = dst.find(f"/projects/{enc(dpath)}")
        if not dp:
            print(f"== {dpath}: not on destination, skipped"); continue
        print(f"== {dpath} -> {sp['path_with_namespace']}")
        d = os.path.join(back, dpath + ".git")
        if os.path.isdir(d):
            r = git(["remote", "update", "--prune"], cwd=d)
        else:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            r = git(["clone", "--mirror", dp["http_url_to_repo"], d])
        if r.returncode:
            print("   FETCH FAILED:", r.stderr.strip()[-400:]); failed.append(dpath); continue
        if have_lfs() and git(["lfs", "ls-files", "--all"], cwd=d).stdout.strip():
            r = git(["lfs", "fetch", "--all", dp["http_url_to_repo"]], cwd=d)
            r2 = git(["lfs", "push", "--all", sp["http_url_to_repo"]], cwd=d)
            if r.returncode or r2.returncode:
                print("   LFS FAILED:", (r.stderr + r2.stderr).strip()[-400:]); failed.append(dpath); continue
        # no --force, no --mirror: never delete or rewrite anything on the source
        r = git(["push", "--all", sp["http_url_to_repo"]], cwd=d)
        r2 = git(["push", "--tags", sp["http_url_to_repo"]], cwd=d)
        rej = [l for l in (r.stderr + r2.stderr).splitlines() if "rejected" in l]
        if rej:
            diverged.append((dpath, rej)); print("   DIVERGED:", *rej, sep="\n     ")
        elif r.returncode or r2.returncode:
            failed.append(dpath); print("   PUSH FAILED:", (r.stderr + r2.stderr).strip()[-400:])
        else:
            print("   ok")
    if diverged:
        print("\nBranches that changed on both sides (merge by hand):")
        for p, lines in diverged:
            print(f"  {p}"); [print("    " + l) for l in lines]
    if failed:
        print("\nFAILED:", *failed, sep="\n  "); sys.exit(1)


# ---------------------------------------------------------------- direct transfer
def cmd_direct_transfer(cfg, args):
    dst = Api(cfg["DST_URL"], cfg["DST_TOKEN"])
    dst.put("/application/settings", {"bulk_import_enabled": True})
    dst_parent, _, leaf = cfg["DST_GROUP"].rpartition("/")
    if dst_parent:
        ensure_group(dst, dst_parent)
    entity = {"source_type": "group_entity", "source_full_path": cfg["SRC_GROUP"],
              "destination_slug": leaf, "destination_namespace": dst_parent}
    if args.migrate_projects is False:
        entity["migrate_projects"] = False
    bi = dst.post("/bulk_imports", {
        "configuration": {"url": cfg["SRC_URL"], "access_token": cfg["SRC_TOKEN"]},
        "entities": [entity],
    })
    print(f"bulk import {bi['id']} started: {bi['status']}")
    while True:
        time.sleep(15)
        bi = dst.get(f"/bulk_imports/{bi['id']}")
        ents = dst.get_all(f"/bulk_imports/{bi['id']}/entities")
        done = sum(1 for e in ents if e["status"] in ("finished", "failed", "timeout"))
        print(f"  {bi['status']:10s} {done}/{len(ents)} entities")
        if bi["status"] in ("finished", "failed", "timeout"):
            break
    bad = [e for e in ents if e["status"] != "finished"]
    for e in bad:
        print(f"  {e['status']:8s} {e['source_full_path']}")
        for f in dst.get_all(f"/bulk_imports/{bi['id']}/entities/{e['id']}/failures"):
            print(f"      {f.get('relation','')}: {f.get('exception_message','')[:200]}")
    print("done" if not bad else f"{len(bad)} entities did not finish — see above; 'repos' still works as a fallback")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default="/root/.gitlab-mirror.env")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    dt = sub.add_parser("direct-transfer")
    dt.add_argument("--no-projects", dest="migrate_projects", action="store_false", default=True)
    rp = sub.add_parser("repos"); rp.add_argument("--only", nargs="*", help="substring filter on project path")
    pb = sub.add_parser("push-back"); pb.add_argument("--only", nargs="*")
    cr = sub.add_parser("cred"); cr.add_argument("op", nargs="?", default="get")
    args = ap.parse_args()
    if args.cmd == "cred" and args.op != "get":
        return
    cfg = load_env(args.env)
    {"list": cmd_list, "repos": cmd_repos, "push-back": cmd_push_back,
     "direct-transfer": cmd_direct_transfer, "cred": cmd_cred}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
