#!/usr/bin/env python3
"""Rename a PIScO profile: replace a station/event ID in every file and
directory name below a profile directory (the directory itself included),
and optionally inside its metadata files.

    profile_rename.py ROOT OLD NEW [--apply] [--no-contents] [--journal DIR]
    profile_rename.py settings.json          # Hub tool mode
    profile_rename.py --undo JOURNAL_DIR

OLD is matched as a whole token: "SO308_1-15-1" matches in
"SO308_1-15-1_PISCO2_..." but not in "SO308_1-15-10_...".

Without --apply nothing is changed; the plan is printed. With --apply,
every rename and every rewritten file is recorded in a journal directory
(renames.tsv + backups of the original metadata files), which --undo
replays backwards. The journal is written as it goes, so an interrupted
run can be undone too, or simply re-run (already renamed entries no
longer match).

Metadata contents: text files (csv, yaml, json, txt, ...) are rewritten
byte-wise; .pickle files are loaded, every string (keys and values) is
rewritten, and they are dumped with the original protocol.
"""
import argparse
import json
import os
import pickle
import re
import shutil
import sys
import time

TEXT_EXT = {".csv", ".yaml", ".yml", ".json", ".txt", ".cfg", ".ini", ".md", ".log", ".tsv"}
PICKLE_EXT = {".pickle", ".pkl"}


def token_re(old):
    # Not preceded by an alphanumeric, not followed by a digit:
    # SO308_1-15-1 must not match SO308_1-15-10.
    return r"(?<![0-9A-Za-z])" + re.escape(old) + r"(?![0-9])"


def plan_renames(root, pat, new):
    """(old_path, new_path) children-first, the root last."""
    plan = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames + dirnames:
            nn = pat.sub(new, name)
            if nn != name:
                plan.append((os.path.join(dirpath, name), os.path.join(dirpath, nn)))
    base = os.path.basename(root)
    nb = pat.sub(new, base)
    if nb != base:
        plan.append((root, os.path.join(os.path.dirname(root), nb)))
    return plan


def check_plan(plan):
    targets = set()
    problems = []
    for old, new in plan:
        if new in targets:
            problems.append(f"two entries would become {new}")
        targets.add(new)
        if os.path.lexists(new):
            problems.append(f"target exists: {new}")
    return problems


def content_files(root):
    out = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in TEXT_EXT or ext in PICKLE_EXT:
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _sub_obj(o, pat, new, n):
    if isinstance(o, str):
        s, k = pat.subn(new, o)
        n[0] += k
        return s
    if isinstance(o, dict):
        return type(o)((_sub_obj(k, pat, new, n), _sub_obj(v, pat, new, n)) for k, v in o.items())
    if isinstance(o, list):
        return [_sub_obj(v, pat, new, n) for v in o]
    if isinstance(o, tuple):
        return tuple(_sub_obj(v, pat, new, n) for v in o)
    return o


def rewrite_content(path, old, new):
    """Return (new_bytes, n_replacements); new_bytes is None if unchanged."""
    raw = open(path, "rb").read()
    if os.path.splitext(path)[1].lower() in PICKLE_EXT:
        proto = raw[1] if raw[:1] == b"\x80" else 0
        n = [0]
        obj = _sub_obj(pickle.loads(raw), re.compile(token_re(old)), new, n)
        if not n[0]:
            return None, 0
        return pickle.dumps(obj, protocol=proto), n[0]
    bpat = re.compile(token_re(old).encode())
    out, k = bpat.subn(new.encode(), raw)
    return (out, k) if k else (None, 0)


def run(root, old, new, apply=False, contents=True, journal=None):
    root = os.path.abspath(root.rstrip("/"))
    if not os.path.isdir(root):
        sys.exit(f"not a directory: {root}")
    if not old or old == new:
        sys.exit("OLD must be non-empty and differ from NEW")
    pat = re.compile(token_re(old))

    plan = plan_renames(root, pat, new)
    edits = []
    if contents:
        for f in content_files(root):
            data, k = rewrite_content(f, old, new)
            if data is not None:
                edits.append((f, data, k))

    print(f"root:     {root}")
    print(f"replace:  {old!r} -> {new!r}")
    print(f"renames:  {len(plan)}")
    for o, n in plan[:3] + (plan[-2:] if len(plan) > 5 else plan[3:]):
        print(f"  {os.path.relpath(o, os.path.dirname(root))}\n    -> {os.path.basename(n)}")
    if len(plan) > 5:
        print(f"  ... ({len(plan) - 5} more)")
    print(f"contents: {len(edits)} file(s)" + ("" if contents else " (disabled)"))
    for f, _, k in edits:
        print(f"  {os.path.relpath(f, root)}: {k} replacement(s)")

    problems = check_plan(plan)
    if problems:
        print("\nABORT, nothing changed:")
        for p in problems[:20]:
            print("  " + p)
        sys.exit(2)
    if not apply:
        print("\ndry run, nothing changed (add --apply)")
        return

    journal = journal or os.path.join(
        os.getcwd(), f"rename_{old}_to_{new}_{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(os.path.join(journal, "backup"), exist_ok=True)
    json.dump({"root": root, "old": old, "new": new, "started": time.time()},
              open(os.path.join(journal, "info.json"), "w"), indent=1)
    print(f"\njournal:  {journal}")

    # Contents first, at the old paths; back up each original before writing.
    with open(os.path.join(journal, "contents.tsv"), "a") as cj:
        for i, (f, data, _) in enumerate(edits):
            bak = os.path.join(journal, "backup", f"{i:04d}_{os.path.basename(f)}")
            shutil.copy2(f, bak)
            cj.write(f"{f}\t{bak}\n")
            cj.flush()
            tmp = f + ".rename-tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            shutil.copystat(bak, tmp)
            os.replace(tmp, f)

    t0 = time.time()
    with open(os.path.join(journal, "renames.tsv"), "a") as rj:
        for i, (o, n) in enumerate(plan, 1):
            os.rename(o, n)
            rj.write(f"{o}\t{n}\n")
            if i % 500 == 0 or i == len(plan):
                rj.flush()
                rate = i / max(time.time() - t0, 1e-6)
                print(f"  renamed {i}/{len(plan)}  ({rate:.0f}/s)", flush=True)
    print("done")


def undo(journal):
    rpath = os.path.join(journal, "renames.tsv")
    if os.path.exists(rpath):
        pairs = [l.rstrip("\n").split("\t") for l in open(rpath) if l.strip()]
        for i, (o, n) in enumerate(reversed(pairs), 1):
            os.rename(n, o)
            if i % 500 == 0 or i == len(pairs):
                print(f"  restored {i}/{len(pairs)} names", flush=True)
    cpath = os.path.join(journal, "contents.tsv")
    if os.path.exists(cpath):
        for line in open(cpath):
            if line.strip():
                f, bak = line.rstrip("\n").split("\t")
                shutil.copy2(bak, f)
                print(f"  restored contents of {f}")
    print("undone")


def main(argv):
    if len(argv) == 1 and argv[0].endswith(".json") and os.path.isfile(argv[0]):
        s = json.load(open(argv[0]))  # Hub tool mode
        return run(s["root"], s["old"], s["new"], apply=s.get("apply", False),
                   contents=s.get("contents", True), journal=s.get("journal") or None)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--undo", metavar="JOURNAL_DIR")
    ap.add_argument("root", nargs="?")
    ap.add_argument("old", nargs="?", help="ID to replace, e.g. SO308_1-15-1")
    ap.add_argument("new", nargs="?", help="replacement, e.g. SO308_1-16-1")
    ap.add_argument("--apply", action="store_true", help="actually change things")
    ap.add_argument("--no-contents", action="store_true", help="names only, leave metadata files as they are")
    ap.add_argument("--journal", help="journal directory (default: ./rename_OLD_to_NEW_<time>)")
    a = ap.parse_args(argv)
    if a.undo:
        return undo(a.undo)
    if not (a.root and a.old and a.new):
        ap.error("ROOT OLD NEW are required")
    run(a.root, a.old, a.new, apply=a.apply, contents=not a.no_contents, journal=a.journal)


if __name__ == "__main__":
    main(sys.argv[1:])
