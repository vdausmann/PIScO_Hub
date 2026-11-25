"""Saved figure-export presets of the Data Explorer: the store and its routes.
Run: python -m pytest tests/pde"""
import json

import pytest

from viewers.pisco_data_explorer.backend.export_presets import PresetError, PresetStore

SETTINGS = {
    "style": {"exp-width": "8.5", "exp-unit": "cm", "exp-fontsize": "8"},
    "title": "{profile}",
    "x": {"from": "concentration_per_L", "to": "Concentration (ind. L<sup>−1</sup>)", "min": "", "max": ""},
    "series": {"detritus": {"name": None, "show": False}},
}


def test_save_list_replace_delete(tmp_path):
    store = PresetStore(tmp_path / "pde" / "export_presets.json")
    assert store.list() == []

    first = store.save("Paper", SETTINGS, "veit")
    assert first["replaced"] is False and first["saved_by"] == "veit"
    store.save("a slide", {"style": {}}, "tim")
    assert [p["name"] for p in store.list()] == ["a slide", "Paper"]      # case-insensitive order

    changed = dict(SETTINGS, title="")
    assert store.save("  Paper ", changed, "veit")["replaced"] is True    # name trimmed: same preset
    paper = next(p for p in store.list() if p["name"] == "Paper")
    assert paper["settings"]["title"] == "" and paper["settings"]["x"]["to"].endswith("</sup>)")

    assert store.delete("Paper") is True
    assert store.delete("Paper") is False
    assert [p["name"] for p in store.list()] == ["a slide"]
    # Written atomically: no temporary files left next to it.
    assert [f.name for f in (tmp_path / "pde").iterdir()] == ["export_presets.json"]


@pytest.mark.parametrize("name,settings", [("", {}), ("   ", {}), ("x" * 81, {}), ("ok", []),
                                           ("ok", {"blob": "x" * 70000})])
def test_rejects_bad_presets(tmp_path, name, settings):
    with pytest.raises(PresetError):
        PresetStore(tmp_path / "p.json").save(name, settings)


def test_unreadable_file_is_an_error_not_an_empty_list(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{not json")
    with pytest.raises(PresetError):
        PresetStore(path).list()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.backend.models import GlobalSetting
    from app.factory import create_app
    app = create_app(create_worker=False)
    app.config["LOGIN_DISABLED"] = True
    real_get = GlobalSetting.get.__func__
    monkeypatch.setattr(GlobalSetting, "get", classmethod(
        lambda cls, key, default=None: str(tmp_path) if key == "DATA_ROOT" else real_get(cls, key, default)))
    with app.app_context():
        yield app.test_client(), tmp_path


def test_routes_round_trip(client):
    client, data_root = client
    base = "/viewer/pisco-data-explorer/export_presets"
    assert client.get(base).get_json() == {"presets": []}

    saved = client.post(base, json={"name": "Paper / depth", "settings": SETTINGS})
    assert saved.status_code == 200 and saved.get_json()["name"] == "Paper / depth"
    stored = json.loads((data_root / "pde" / "export_presets.json").read_text())
    assert stored["presets"]["Paper / depth"]["settings"] == SETTINGS

    assert client.post(base, json={"name": "", "settings": {}}).status_code == 400
    assert client.post(base, data="nonsense").status_code == 400

    listed = client.get(base).get_json()["presets"]
    assert [p["name"] for p in listed] == ["Paper / depth"]

    assert client.delete(f"{base}/Paper / depth").status_code == 204       # a slash in the name
    assert client.delete(f"{base}/Paper / depth").status_code == 404
    assert client.get(base).get_json() == {"presets": []}
