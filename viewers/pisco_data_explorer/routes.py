import json
import pathlib

from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import login_required
from werkzeug.exceptions import BadRequest

from app.backend.models import GlobalSetting
from .backend import hub_profiles, pde

config_path = pathlib.Path(__file__).parent.resolve()
with open(config_path / "config.json") as f:
    config = json.load(f)

name = config["name"]
url = name.lower().replace("_", "-").replace(" ", "-")

pde_bp = Blueprint(
    url,
    __name__,
    template_folder="templates",
    url_prefix="/viewer/" + url,
    static_folder="static",
    static_url_path="/static"
)


# ─── Defaults ─────────────────────────────────────────────────────────────────
# Deployment-specific paths live in the global settings (PDE_ROOT,
# PDE_CACHE_ROOT). An empty PDE_ROOT falls back to the file explorer root,
# which is where users find their EcoTaxa TSVs anyway.

def default_root() -> str:
    value = (GlobalSetting.get("PDE_ROOT", "") or "").strip()
    if value:
        return value
    return (GlobalSetting.get("FILE_EXPLORER_ROOT", "") or "").strip() or pde.DEFAULT_ROOT


def default_cache_root() -> str:
    return (GlobalSetting.get("PDE_CACHE_ROOT", "") or "").strip() or pde.DEFAULT_CACHE_ROOT


def hub_mask_radius():
    """PDE_MASK_RADIUS_PX as a number, or None while it is unset."""
    try:
        value = float(str(GlobalSetting.get("PDE_MASK_RADIUS_PX", "") or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def hub_frame_source(path):
    """Volumes for a TSV the Hub exported: its workflow's frames times the
    per-frame volume of PDE_MASK_RADIUS_PX. The C++ segmenter applies no
    field-of-view mask, so there is no radius to read from the run."""
    found = hub_profiles.frame_pressures(path)
    if found is None:
        return None
    pressures, source, warning = found
    radius = hub_mask_radius()
    if radius is None:
        print(f"[PDE] {len(pressures)} frames from {source}, but PDE_MASK_RADIUS_PX is unset")
        return None
    volumes = pde.volumes_from_pressures(pressures, pde.calculate_volume(radius))
    return volumes, f"{len(pressures):,} frames from {source}, r = {radius:g} px", warning


def load_path(path, cache_root, cruise):
    return pde.load_tsv_path(pathlib.Path(path), cache_root, cruise,
                             frame_source=hub_frame_source)


# ─── Load results ─────────────────────────────────────────────────────────────

def _metadata(cache_key: str, res: dict) -> dict:
    """Small summary of a load; the raw DataFrame stays server-side."""
    df = res["df"]
    categories = []
    if "object_annotation_category" in df.columns:
        categories = sorted(df["object_annotation_category"].dropna().unique().tolist())

    n_profiles = len(res["profiles"])
    n_volumes = len(res["volumes"])
    status = f"{len(df):,} rows from {n_profiles} profile(s)"
    if n_volumes < n_profiles:
        status += (f" — ⚠️ no sampled volume for {n_profiles - n_volumes} profile(s), "
                   "so no concentrations")
        if res.get("volume_source") is None and hub_mask_radius() is None:
            status += " (set PDE_MASK_RADIUS_PX for profiles processed in the Hub)"
    elif res.get("volume_source"):
        status += f" — volumes: {res['volume_source']}"
    if res.get("volume_warning"):
        status += f" — ⚠️ {res['volume_warning']}"

    return {
        "cache_key": cache_key,
        "profiles": res["profiles"],
        "n_rows": int(len(df)),
        "categories": categories,
        "columns": pde.PROFILE_COLUMNS,
        "volumes": {p: int(len(v)) for p, v in res["volumes"].items()},
        "status": status,
    }


# ─── Routes ───────────────────────────────────────────────────────────────────

@pde_bp.route("/")
@login_required
def index():
    """The page. Supports deep links:
        ?path=/…/ecotaxa_x.tsv      a TSV straight from the file explorer
        ?root=…&cruise=…&profile=…  one server profile
        ?root=…&cruise=…            cruise selected, pick a profile in the UI
    """
    root = (request.args.get("root") or default_root()).strip()
    cache_root = (request.args.get("cache_root") or default_cache_root()).strip()
    cruise = request.args.get("cruise") or None
    profile = request.args.get("profile") or None
    path = request.args.get("path") or None

    initial = {"root": root, "cache_root": cache_root, "cruise": cruise, "path": path,
               "loaded": None}

    if path:
        try:
            res = load_path(path, cache_root, cruise)
            initial["loaded"] = _metadata(f"path|{pathlib.Path(path).resolve()}", res)
            pde._remember(initial["loaded"]["cache_key"], res["df"], res["volumes"])
        except Exception as e:
            print(f"[PDE] deep link load failed: {e}")
            initial["load_error"] = str(e)
    elif profile and cruise and root:
        profiles_dir = pde.detect_profiles_dir(root, cruise)
        if not profiles_dir:
            initial["load_error"] = f"no profiles folder in {root}/{cruise}"
        else:
            try:
                res = pde.load_profiles(profiles_dir, [profile], cache_root, cruise)
                key = "|".join(sorted(res["profiles"]))
                initial["loaded"] = _metadata(key, res)
                pde._remember(key, res["df"], res["volumes"])
            except Exception as e:
                print(f"[PDE] profile load failed: {e}")
                initial["load_error"] = str(e)

    return render_template("pisco_data_explorer.html", initial=initial)


@pde_bp.route("/scan")
@login_required
def scan():
    """List cruises under the root path and report portable-cache status."""
    root = (request.args.get("root") or default_root()).strip()
    cache_root = (request.args.get("cache_root") or default_cache_root()).strip()

    cruises = pde.get_cruise_list(root)
    cache_dir = pathlib.Path(cache_root).expanduser()
    cache_count = len(pde.get_cached_profiles(cache_root, cruise=None)) if cache_dir.exists() else 0

    return jsonify({
        "cruises": cruises,
        "auto": cruises[0] if len(cruises) == 1 else None,
        "root_status": (f"Found {len(cruises)} cruise(s)" if cruises
                        else f"No directories found at '{root}'"),
        "cache_status": (f"Portable cache: {cache_count} profile(s) at {cache_dir}"
                         if cache_dir.exists()
                         else f"Portable cache will be created at {cache_dir}"),
    })


@pde_bp.route("/profiles")
@login_required
def profiles():
    """Profiles with an EcoTaxa TSV, plus profiles known to the cache."""
    root = (request.args.get("root") or default_root()).strip()
    cruise = request.args.get("cruise") or None
    cache_root = (request.args.get("cache_root") or default_cache_root()).strip()

    server, profiles_dir = [], None
    if cruise and root:
        profiles_dir = pde.detect_profiles_dir(root, cruise)
        if profiles_dir:
            server = pde.get_available_profiles(profiles_dir)

    return jsonify({
        "profiles_dir": profiles_dir,
        "server": server,
        "cached": pde.get_cached_profiles(cache_root, cruise) if cruise else [],
        "merged": sorted(set(server) | set(pde.get_cached_profiles(cache_root, cruise) if cruise else [])),
        "status": (f"{len(server)} profile(s) in …/{cruise}/{pathlib.Path(profiles_dir).name}/"
                   if profiles_dir and cruise else ""),
    })


@pde_bp.route("/hub_exports")
@login_required
def hub_exports():
    """EcoTaxa TSVs the Hub's own export tasks wrote, newest first."""
    return jsonify({"exports": hub_profiles.hub_exports(),
                    "mask_radius": hub_mask_radius()})


@pde_bp.route("/choices")
@login_required
def choices():
    """The exports as links, for the Visualization Hub's card."""
    return jsonify({"choices": [
        {"label": f"{e['workflow']} · {e['created'].replace('T', ' ')}" if e["created"] else e["workflow"],
         "url": url_for(".index", path=e["path"])}
        for e in hub_profiles.hub_exports()]})


@pde_bp.route("/load")
@login_required
def load():
    """Load TSV(s) server-side; returns the summary, keeps the DataFrame."""
    path = request.args.get("path")
    cruise = request.args.get("cruise") or None
    root = (request.args.get("root") or default_root()).strip()
    cache_root = (request.args.get("cache_root") or default_cache_root()).strip()
    profile_names = [p.strip() for p in (request.args.get("profiles") or "").split(",")
                     if p.strip()]

    if path:
        try:
            res = load_path(path, cache_root, cruise)
        except (FileNotFoundError, ValueError) as e:
            return jsonify({"error": str(e)}), 400
        key = f"path|{pathlib.Path(path).resolve()}"
    elif profile_names and cruise and root:
        profiles_dir = pde.detect_profiles_dir(root, cruise)
        if not profiles_dir:
            return jsonify({"error": f"no profiles folder in {root}/{cruise}"}), 400
        try:
            res = pde.load_profiles(profiles_dir, profile_names, cache_root, cruise)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        key = "|".join(sorted(res["profiles"]))
    else:
        raise BadRequest("give 'path' or 'cruise' + 'profiles'")

    pde._remember(key, res["df"], res["volumes"])
    return jsonify(_metadata(key, res))


@pde_bp.route("/plot")
@login_required
def plot():
    """Build the figure + data preview for the current selection."""
    cache_key = request.args.get("cache_key")
    entry = pde.get_cached(cache_key)
    if entry is None:
        return jsonify({"error": "data not in server cache — please reload"}), 404

    try:
        edges = json.loads(request.args.get("edges") or "[]")
        edges = [float(e) for e in edges]
    except (ValueError, TypeError):
        raise BadRequest("edges must be a JSON list of numbers")
    if not edges or len(edges) < 2:
        edges = [0, 100, 1000, 5000]

    plot_type = request.args.get("plot_type", "depth")
    options = [o for o in (request.args.get("options") or "").split(",") if o]
    taxon = [t for t in (request.args.get("taxon") or "").split(",") if t]

    try:
        result = pde.render_plot(
            entry["df"], entry["volumes"], edges, plot_type,
            x_axis=request.args.get("x_axis") or None,
            y_axis=request.args.get("y_axis") or None,
            color_col=request.args.get("color") or None,
            plot_options=options,
            taxon_filter=taxon,
            validated_only=request.args.get("validated") in ("1", "true"),
            esd_min=float(request.args.get("esd_min") or 50),
            esd_max=float(request.args.get("esd_max") or 10000),
            esd_nbins=int(request.args.get("esd_nbins") or 25),
            esd_yaxis=request.args.get("esd_yaxis") or "biovolume_mm3_per_L",
        )
    except Exception as e:
        print(f"[PDE] plot failed: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify(result)
