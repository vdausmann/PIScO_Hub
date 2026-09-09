from .models import GlobalSetting, Workflow
from pathlib import Path
import os
import re


# Task output is filed under one directory per workflow, named by its id.
# That is unreadable when browsing, so the listing carries the workflow's
# name alongside it -- the same name the processing view shows.
_WORKFLOW_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE)


def _workflow_labels(entries):
    """Names for whichever directory entries are workflow ids, in one query."""
    ids = [e["name"] for e in entries
           if e["type"] == "directory" and _WORKFLOW_ID.match(e["name"])]

    if not ids:
        return {}

    try:
        rows = Workflow.query.filter(Workflow.id.in_(ids)).all()
    except Exception:
        # Browsing files should not fail because a lookup did.
        return {}

    return {workflow.id: workflow.name for workflow in rows if workflow.name}


def get_files(_path: str, show_hidden: bool = False):
    root = GlobalSetting.get("FILE_EXPLORER_ROOT", default="/")

    if not type(root) == str:
        raise ValueError("FILE_EXPLORER_ROOT must be a string")

    root = Path(root).resolve()

    if not os.path.exists(root):
        raise ValueError("FILE_EXPLORER_ROOT does not exist")

    path = (root / _path).resolve()

    if not str(path).startswith(str(root)):
        raise ValueError("Path outside root")

    if not os.path.exists(path):
        raise ValueError("Path does not exist")

    entries = []

    for item in sorted(path.iterdir()):

        try: 
            stat = item.stat()

            if not show_hidden and item.name.startswith("."):
                continue

            entries.append({
                "name": item.name,
                "label": None,
                "type": ("directory" if item.is_dir() else "file"),
                "extension": (item.suffix if item.is_file() else None),
                "size": (stat.st_size if item.is_file() else None),
                "last_modified": stat.st_mtime,
                "path": str(item)
            })
        except:
            ...


    # folders first:
    folders = [e for e in entries if e["type"] == "directory"]
    files = [e for e in entries if e["type"] == "file"]

    folders.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x["name"].lower())

    entries = folders + files

    labels = _workflow_labels(entries)
    for entry in entries:
        entry["label"] = labels.get(entry["name"])

    return {
            "current_path": str(path.relative_to(root)),
            "entries": entries
            }



