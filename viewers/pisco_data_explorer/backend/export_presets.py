"""Saved figure-export presets of the Data Explorer.

A preset is what the export dialog holds — size, fonts, format, legend
position, and the labels with the axis titles they replace — stored
under a name so it can be applied to the next profile. They live in one
JSON file on the Hub, not in the browser, so they are the same for every
browser, host name and user of the Hub.
"""
import json
import os
import pathlib
import tempfile
import threading
from datetime import datetime, timezone

MAX_NAME = 80
MAX_BYTES = 64 * 1024          # one preset's settings; a few KB in practice

_lock = threading.Lock()


class PresetError(ValueError):
    pass


class PresetStore:
    def __init__(self, path):
        self.path = pathlib.Path(path)

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            raise PresetError(f"cannot read {self.path}: {e}")
        return data.get("presets", {}) if isinstance(data, dict) else {}

    def _write(self, presets: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".export_presets.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"presets": presets}, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise

    def list(self) -> list:
        with _lock:
            presets = self._read()
        return [{"name": name, **entry} for name, entry in sorted(presets.items(), key=lambda kv: kv[0].lower())]

    def save(self, name, settings, user=None) -> dict:
        name = (name or "").strip() if isinstance(name, str) else ""
        if not name:
            raise PresetError("a preset needs a name")
        if len(name) > MAX_NAME:
            raise PresetError(f"the name is longer than {MAX_NAME} characters")
        if not isinstance(settings, dict):
            raise PresetError("settings must be an object")
        if len(json.dumps(settings)) > MAX_BYTES:
            raise PresetError("the settings are too large")
        entry = {"settings": settings, "saved_by": user,
                 "saved": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with _lock:
            presets = self._read()
            existed = name in presets
            presets[name] = entry
            self._write(presets)
        return {"name": name, "replaced": existed, **entry}

    def delete(self, name) -> bool:
        with _lock:
            presets = self._read()
            if name not in presets:
                return False
            del presets[name]
            self._write(presets)
        return True
