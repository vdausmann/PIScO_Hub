from .models import GlobalSetting
from pathlib import Path
import os


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

        stat = item.stat()

        if not show_hidden and item.name.startswith("."):
            continue

        entries.append({
            "name": item.name,
            "type": ("directory" if item.is_dir() else "file"),
            "extension": (item.suffix if item.is_file() else None),
            "size": (stat.st_size if item.is_file() else None),
            "last_modified": stat.st_mtime,
            "path": str(item)
        })


    # folders first:
    folders = [e for e in entries if e["type"] == "directory"]
    files = [e for e in entries if e["type"] == "file"]

    folders.sort(key=lambda x: x["name"].lower())
    files.sort(key=lambda x: x["name"].lower())

    entries = folders + files

    return {
            "current_path": str(path.relative_to(root)),
            "entries": entries
            }



