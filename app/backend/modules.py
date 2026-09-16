"""External modules shown in the Visualization Hub.

A module is a web page that lives somewhere else — a colleague's GitLab
Pages site, a dashboard on another host — that the Hub surfaces as a card
and, by default, frames inside its own layout. The Hub does not run or
proxy anything: it only knows the address.

Modules are declared in the JSON file named by the MODULES_FILE setting
(default: modules.json in the Hub root), a list of objects:

    name         short slug used in the Hub's URL; letters, digits, - and _
    title        card heading
    description  one or two sentences for the card
    mode         "embed" (default) frames the url at /visualization/m/<name>;
                 "link" sends the browser straight to the url (for sites
                 that refuse to be framed, or need their own tab);
                 "static" serves a directory on the Hub's own disk — a
                 site a tool wrote, e.g. the satellite gallery — and
                 frames its index.html
    url          where the module lives (http or https); embed and link
    path         the directory to serve; static only. Relative paths are
                 taken from the Hub root
    source       optional: repository or project URL, shown on the card
    contact      optional: who to ask about it

The file is read on every request so an edit shows up without a restart.
"""
import json
import os
import re

from flask import current_app

from .models import GlobalSetting

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_MODES = ("embed", "link", "static")


def modules_file():
    path = GlobalSetting.get("MODULES_FILE", "modules.json")
    if not os.path.isabs(path):
        path = os.path.join(current_app.config["HUB_ROOT"], path)
    return path


def load_modules():
    """The declared modules, in file order. A missing file means none;
    a malformed one raises so the mistake is visible rather than silently
    emptying the page."""
    path = modules_file()
    if not os.path.exists(path):
        return []
    with open(path) as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of modules")

    modules = []
    seen = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: module #{i} is not an object")
        name = entry.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"{path}: module #{i} needs a 'name' slug")
        if name in seen:
            raise ValueError(f"{path}: duplicate module name '{name}'")
        seen.add(name)
        mode = entry.get("mode", "embed")
        if mode not in _MODES:
            raise ValueError(f"{path}: module '{name}': mode must be one of {_MODES}")
        url = entry.get("url")
        directory = entry.get("path")
        if mode == "static":
            if not isinstance(directory, str) or not directory:
                raise ValueError(f"{path}: module '{name}' needs a 'path' (mode static)")
            if not os.path.isabs(directory):
                directory = os.path.join(current_app.config["HUB_ROOT"], directory)
        elif not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError(f"{path}: module '{name}' needs an http(s) 'url'")
        modules.append({
            "name": name,
            "title": entry.get("title") or name,
            "description": entry.get("description", ""),
            "url": url,
            "path": directory,
            "mode": mode,
            "source": entry.get("source"),
            "contact": entry.get("contact"),
        })
    return modules


def get_module(name):
    for module in load_modules():
        if module["name"] == name:
            return module
    return None
