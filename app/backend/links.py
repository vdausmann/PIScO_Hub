"""Links on the landing page to services elsewhere on the ship's network.

EcoTaxa, a GitLab mirror, camera dashboards: other machines' web pages
that people on board want one click away. The Hub only shows the links.

They are declared in the JSON file named by the LINKS_FILE setting
(default: links.json in the Hub root; it is ship-specific, so not in the
repository - links.example.json shows the shape), a list of objects:

    title        what the link says
    url          where it goes (http or https)
    description  optional: one line under the title
    icon         optional pictogram: plankton, camera, git, gauge; anything
                 else (or none) draws a plain outward arrow

The file is read on every request so an edit shows up without a restart.
"""
import json
import os

from flask import current_app

from .models import GlobalSetting


def links_file():
    path = GlobalSetting.get("LINKS_FILE", "links.json")
    if not os.path.isabs(path):
        path = os.path.join(current_app.config["HUB_ROOT"], path)
    return path


def load_links():
    """The declared links, in file order. A missing file means none; a
    malformed one raises so the mistake is visible."""
    path = links_file()
    if not os.path.exists(path):
        return []
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of links")
    links = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or not entry.get("title") or not entry.get("url"):
            raise ValueError(f"{path}: entry {i + 1} needs a title and a url")
        if not str(entry["url"]).startswith(("http://", "https://")):
            raise ValueError(f"{path}: {entry['title']}: url must start with http:// or https://")
        links.append({"title": str(entry["title"]), "url": str(entry["url"]),
                      "description": str(entry.get("description") or ""),
                      "icon": str(entry.get("icon") or "")})
    return links
