"""Data layer of the PIScO Data Explorer viewer.

Ported from the standalone Dash app (team-optics/plankton/PIScO_data_explorer,
plotting_app.py) with the Dash glue removed: the same pandas/plotly core that
built binned depth profiles and ESD size spectra now serves plain Flask JSON
endpoints.

Server-side cache
-----------------
Raw DataFrames (a profile export can be a million rows) stay in _data_cache;
only small summaries cross the wire.

Figures are built as plain plotly.js dicts rather than with the plotly
package: the browser has the library already, and a viewer that fails to
import takes no page with it, but one more server dependency is one more
thing the ship's Hub (no internet) may not have.
"""
import json
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ─── Defaults ─────────────────────────────────────────────────────────────────
DEFAULT_ROOT = "/mnt/filer"
DEFAULT_CACHE_ROOT = "/mnt/filer/M181/M181_Profiles_cached"

MICRONS_PER_PIXEL = 22.5
DEPTH_MM = 500

# Binning presets for the depth profile plot.
PRESETS = {
    "coarse":   [0, 200, 1000, 4000, 10000],
    "medium":   "0:50:50, 50:200:50, 200:1000:200, 1000:4000:500, 4000:10000:1000",
    "fine":     "0:100:10, 100:1000:50, 1000:5000:100",
    "veryfine": "0:100:5, 100:500:25, 500:2000:50",
    "1dbar":    "0:1000:1",
    "10dbar":   "0:5000:10",
}

# Columns offered in the x/y dropdowns of the depth profile plot.
PROFILE_COLUMNS = [
    "depth_bin_str", "depth_bin_midpoint", "object_annotation_category",
    "count", "biovolume_mm3", "sampled_volume_L",
    "concentration_per_L", "biovolume_mm3_per_L",
    "esd_mean_um", "esd_median_um", "esd_std_um",
    "pressure_mean_dbar", "n_profiles",
]

# ─── Server-side data cache ───────────────────────────────────────────────────
# cache_key → {"df": DataFrame, "volumes": {profile: {pressure_dbar: litres}}}
_data_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_LIMIT = 4  # keep only the most recent loads; a million rows is not free


def _remember(cache_key: str, df: pd.DataFrame, volumes: dict):
    with _cache_lock:
        _data_cache[cache_key] = {"df": df, "volumes": volumes}
        while len(_data_cache) > _CACHE_LIMIT:
            oldest = next(iter(_data_cache))
            if oldest == cache_key:
                break
            _data_cache.pop(oldest)


def get_cached(cache_key: str):
    with _cache_lock:
        return _data_cache.get(cache_key)


# ─── Filename utilities (identical to the Dash app) ───────────────────────────

def extract_pressure_from_filename(filename: str):
    """Return pressure in bar from a PIScO PNG filename, or None."""
    match = re.search(r'(\d+\.\d+)bar', filename)
    return float(match.group(1)) if match else None


def extract_timestamp_from_filename(filename: str):
    """Return image timestamp token like YYYYMMDD-HHMMSSff, or None."""
    match = re.search(r'(\d{8}-\d{8})', filename)
    return match.group(1) if match else None


def parse_timestamp_to_iso(timestamp_token: str):
    if not timestamp_token:
        return None
    try:
        return datetime.strptime(timestamp_token, "%Y%m%d-%H%M%S%f").isoformat()
    except ValueError:
        return None


def parse_timestamp_to_unix_s(timestamp_token: str):
    if not timestamp_token:
        return None
    try:
        return datetime.strptime(timestamp_token, "%Y%m%d-%H%M%S%f").timestamp()
    except ValueError:
        return None


def get_mask_radius(profile_path: Path):
    """Read mask_radius (pixels) from {profile}_Results/settings.csv."""
    settings_file = profile_path / f"{profile_path.name}_Results" / "settings.csv"
    if not settings_file.exists():
        return None
    try:
        df = pd.read_csv(settings_file)
        if "Field Name" in df.columns and "Value" in df.columns:
            row = df[df["Field Name"] == "mask_radius"]
            if not row.empty:
                return float(row["Value"].iloc[0])
    except Exception as e:
        print(f"[SETTINGS] Error: {e}")
    return None


def calculate_volume(mask_radius_pixels: float) -> float:
    """Return sampled volume per image in litres."""
    radius_mm = (mask_radius_pixels * MICRONS_PER_PIXEL) / 1000
    return np.pi * (radius_mm ** 2) * DEPTH_MM / 1_000_000


def volumes_from_pressures(pressures, vol_per_image: float) -> dict:
    """{pressure_dbar: sampled_volume_L} from one pressure per frame,
    rounded to 0.1 dbar like the PNG scan."""
    counts = pd.Series(np.round(np.asarray(pressures, dtype=float), 1)).value_counts()
    return {float(p): int(c) * vol_per_image for p, c in counts.items()}


# ─── Portable image-metadata cache ────────────────────────────────────────────

def _safe_name(name: str) -> str:
    """Filesystem-safe cruise/profile key part."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name or "").strip())


def _cache_candidates(cache_root: str, profile_name: str, cruise: str = None) -> list:
    """Candidate CSV cache paths in lookup priority order."""
    if not cache_root or not profile_name:
        return []
    root = Path(cache_root).expanduser()
    profile_key = _safe_name(profile_name)
    candidates = []
    if cruise:
        candidates.append(root / _safe_name(cruise) / profile_key / "image_metadata.csv")
    candidates.append(root / "_global" / profile_key / "image_metadata.csv")
    return candidates


def _resolve_cache_csv(cache_root: str, profile_name: str, cruise: str = None):
    for candidate in _cache_candidates(cache_root, profile_name, cruise):
        if candidate.exists():
            return candidate
    root = Path(cache_root or "").expanduser()
    if root.exists():
        matches = sorted(root.glob(f"*/{_safe_name(profile_name)}/image_metadata.csv"))
        if matches:
            return matches[0]
    return None


def _volumes_from_metadata_df(df_meta: pd.DataFrame, profile_path: Path = None):
    """Convert image metadata rows into {pressure_dbar: sampled_volume_L}."""
    if df_meta.empty or "pressure_dbar" not in df_meta.columns:
        return None

    vol_per_image = None
    if "vol_per_image_L" in df_meta.columns:
        series = pd.to_numeric(df_meta["vol_per_image_L"], errors="coerce").dropna()
        if not series.empty and float(series.iloc[0]) > 0:
            vol_per_image = float(series.iloc[0])

    if vol_per_image is None and "mask_radius_pixels" in df_meta.columns:
        series = pd.to_numeric(df_meta["mask_radius_pixels"], errors="coerce").dropna()
        if not series.empty and float(series.iloc[0]) > 0:
            vol_per_image = calculate_volume(float(series.iloc[0]))

    if vol_per_image is None and profile_path is not None:
        mask_radius = get_mask_radius(profile_path)
        if mask_radius is not None:
            vol_per_image = calculate_volume(mask_radius)

    if vol_per_image is None:
        return None

    pressure_counts = pd.to_numeric(df_meta["pressure_dbar"], errors="coerce").dropna().value_counts().to_dict()
    if not pressure_counts:
        return None
    return {float(p): c * vol_per_image for p, c in pressure_counts.items()}


def create_image_metadata_csv(
    profile_path: Path,
    force_regenerate: bool = False,
    output_csv_path: Path = None,
    cruise: str = None,
):
    """Scan PNG files in a profile and save metadata to a CSV cache.
    Returns the path of the created/used CSV, or None."""
    profile_name = profile_path.name
    if output_csv_path is None:
        results_dir = profile_path / f"{profile_name}_Results"
        results_dir.mkdir(exist_ok=True)
        csv_path = results_dir / "image_metadata.csv"
    else:
        csv_path = Path(output_csv_path)
        csv_path.parent.mkdir(parents=True, exist_ok=True)

    if csv_path.exists() and not force_regenerate:
        return csv_path

    mask_radius = get_mask_radius(profile_path)
    vol_per_image = calculate_volume(mask_radius) if mask_radius is not None else np.nan

    png_dir = profile_path / "PNG"
    if not png_dir.exists():
        png_dir = next((p for p in profile_path.glob("**/PNG") if p.is_dir()), None)
    if not png_dir or not png_dir.exists():
        print(f"[CACHE] No PNG directory found for {profile_name}")
        return None

    png_files = list(png_dir.glob("*.png"))
    if not png_files:
        print(f"[CACHE] No PNG files found for {profile_name}")
        return None

    print(f"[CACHE] Scanning {len(png_files)} PNGs for {profile_name}...")
    records = []
    for img_path in png_files:
        p_bar = extract_pressure_from_filename(img_path.name)
        ts = extract_timestamp_from_filename(img_path.name)
        if p_bar is not None:
            records.append({
                "image_filename": img_path.name,
                "image_timestamp": ts,
                "image_datetime_iso": parse_timestamp_to_iso(ts),
                "image_unix_s": parse_timestamp_to_unix_s(ts),
                "pressure_bar": p_bar,
                "pressure_dbar": round(p_bar * 10, 1),
                "mask_radius_pixels": mask_radius,
                "vol_per_image_L": vol_per_image,
                "profile": profile_name,
                "cruise": cruise or "",
            })

    if not records:
        print("[CACHE] No valid pressure data extracted from filenames")
        return None

    df_meta = pd.DataFrame(records)
    df_meta.to_csv(csv_path, index=False)
    print(f"[CACHE] Created: {csv_path} ({len(records)} images)")
    return csv_path


def load_image_volumes_from_csv(csv_path: Path, profile_path: Path = None):
    """Load {pressure_dbar: sampled_volume_L} from a cached metadata CSV."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return None
    try:
        df_meta = pd.read_csv(csv_path)
        return _volumes_from_metadata_df(df_meta, profile_path=profile_path)
    except Exception as e:
        print(f"[CACHE] Error reading CSV {csv_path}: {e}")
        return None


def load_image_volumes_from_cache_root(cache_root: str, profile_name: str, cruise: str = None):
    """Load {pressure_dbar: sampled_volume_L} from the portable cache root."""
    csv_path = _resolve_cache_csv(cache_root, profile_name, cruise)
    if not csv_path:
        return None
    return load_image_volumes_from_csv(csv_path, profile_path=None)


def get_cached_profiles(cache_root: str, cruise: str = None) -> list:
    """List profile names available from portable cache-root CSVs."""
    root = Path(cache_root or "").expanduser()
    if not root.exists():
        return []

    profile_names = set()
    if cruise:
        cruise_dir = root / _safe_name(cruise)
        if cruise_dir.exists():
            for csv_file in cruise_dir.glob("*/image_metadata.csv"):
                profile_names.add(csv_file.parent.name)

    global_dir = root / "_global"
    if global_dir.exists():
        for csv_file in global_dir.glob("*/image_metadata.csv"):
            profile_names.add(csv_file.parent.name)

    if not cruise:
        for csv_file in root.glob("*/*/image_metadata.csv"):
            profile_names.add(csv_file.parent.name)

    return sorted(profile_names)


def calculate_profile_volumes(profile_path: Path, cache_root: str = "", cruise: str = None):
    """Build {pressure_dbar: sampled_volume_L} in this order:
    1) portable cache-root CSV
    2) local profile CSV
    3) PNG scan fallback
    Returns None if essential data is missing."""
    profile_path = Path(profile_path)
    profile_name = profile_path.name

    volumes = load_image_volumes_from_cache_root(cache_root, profile_name, cruise)
    if volumes is not None:
        print(f"[VOLUME_CALC] Used portable cache for {profile_name}")
        return volumes

    local_csv = profile_path / f"{profile_name}_Results" / "image_metadata.csv"
    volumes = load_image_volumes_from_csv(local_csv, profile_path=profile_path)
    if volumes is not None:
        print(f"[VOLUME_CALC] Used local CSV cache for {profile_name}")
        return volumes

    print(f"[VOLUME_CALC] No CSV cache, scanning filesystem for {profile_name}")
    mask_radius = get_mask_radius(profile_path)
    if mask_radius is None:
        print(f"[VOLUME_CALC] Cannot read mask_radius for {profile_name}")
        return None

    vol_per_image = calculate_volume(mask_radius)

    png_dir = profile_path / "PNG"
    if png_dir.exists():
        png_files = list(png_dir.glob("*.png"))
    else:
        png_files = list(profile_path.glob("**/PNG/*.png"))
    if not png_files:
        print(f"[VOLUME_CALC] No PNG files found for {profile_name}")
        return None

    pressure_counts: dict = {}
    for img in png_files:
        p_bar = extract_pressure_from_filename(img.name)
        if p_bar is not None:
            p_dbar = round(p_bar * 10, 1)
            pressure_counts[p_dbar] = pressure_counts.get(p_dbar, 0) + 1
    if not pressure_counts:
        print(f"[VOLUME_CALC] No valid pressures extracted for {profile_name}")
        return None

    volume_dict = {p: c * vol_per_image for p, c in pressure_counts.items()}

    # Cache what we just computed, locally and in the portable cache.
    create_image_metadata_csv(profile_path, force_regenerate=False, cruise=cruise)
    if cache_root:
        for candidate in _cache_candidates(cache_root, profile_name, cruise):
            create_image_metadata_csv(
                profile_path, force_regenerate=False,
                output_csv_path=candidate, cruise=cruise,
            )
    return volume_dict


# ─── Cruise / profile discovery (identical to the Dash app) ──────────────────

def get_cruise_list(root_path: str) -> list:
    """Sorted subdirectory names under root_path (one per cruise)."""
    p = Path(root_path)
    if not p.exists():
        return []
    return sorted(d.name for d in p.iterdir() if d.is_dir())


def detect_profiles_dir(root_path: str, cruise: str):
    """Auto-detect the PISCO-Profiles subdirectory inside a cruise folder."""
    cruise_dir = Path(root_path) / cruise
    if not cruise_dir.exists():
        return None
    for d in sorted(cruise_dir.iterdir()):
        if d.is_dir() and ("PISCO-Profiles" in d.name or "Profiles" in d.name):
            return str(d)
    return str(cruise_dir)  # fallback: cruise dir itself


def _profile_tsv_candidates(profile_dir: Path):
    """The two naming conventions seen in the wild (old and new pipeline)."""
    name = profile_dir.name
    results = profile_dir / f"{name}_Results" / "EcoTaxa"
    return [
        results / f"{name}_ecotaxa.tsv",
        results / f"ecotaxa_{name}.tsv",
    ]


def get_available_profiles(base_dir: str) -> list:
    """Sorted list of profiles that have an EcoTaxa TSV under base_dir."""
    p = Path(base_dir)
    if not p.exists():
        return []
    profiles = []
    for d in p.iterdir():
        if d.is_dir() and any(c.exists() for c in _profile_tsv_candidates(d)):
            profiles.append(d.name)
    return sorted(profiles)


# ─── TSV loading ──────────────────────────────────────────────────────────────

_TYPE_ROW_VALUES = {"[t]", "[f]"}


def read_ecotaxa_tsv(path: Path) -> pd.DataFrame:
    """Read an EcoTaxa export TSV.

    On-disk exports carry a second row of column types ('[t]'/'[f]'); the
    Dash app worked around it with skiprows=[1] for server files and not for
    uploads. Detect that row before parsing and skip it, so both work —
    dropping it afterwards would leave every column parsed as text.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        f.readline()
        second = f.readline().rstrip("\r\n").split("\t")
    has_type_row = all(v.strip() in _TYPE_ROW_VALUES for v in second)
    return pd.read_csv(path, sep="\t", skiprows=[1] if has_type_row else None)


def _prepare(df: pd.DataFrame, profile: str) -> pd.DataFrame:
    """Shared normalisation: profile label + biovolume from ESD."""
    if "object_pressure" not in df.columns:
        raise ValueError("missing 'object_pressure' column")
    if "object_esd" not in df.columns:
        df["object_esd"] = np.nan
    df["profile"] = profile
    df["biovolume_mm3"] = (np.pi / 6) * ((df["object_esd"] / 1000) ** 3)
    return df


def profile_name_from_tsv(path: Path) -> str:
    """Best-effort profile name for a TSV: from the filename convention,
    else from the enclosing profile directory, else the file stem."""
    name = path.name
    m = re.match(r"^(.+)_(?:ecotaxa|ecotaxa)\.tsv$", name, re.IGNORECASE)
    if m and m.group(1):
        return m.group(1)
    m = re.match(r"^ecotaxa_(.+)\.tsv$", name, re.IGNORECASE)
    if m and m.group(1):
        return m.group(1)
    # .../{profile}/{profile}_Results/EcoTaxa/anything.tsv
    results_dir = path.parent.parent
    if results_dir.name.endswith("_Results"):
        return results_dir.name[: -len("_Results")]
    return path.stem


def load_tsv_path(path: Path, cache_root: str, cruise: str = None,
                  frame_source=None) -> dict:
    """Load one TSV by path (file-explorer deep link). Returns a load result.

    frame_source(path) is asked for the frames of a TSV that is neither in
    the portable cache nor inside a profile's _Results tree (a Hub export):
    it returns (volumes, source, warning) or None.
    """
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")

    profile = profile_name_from_tsv(path)
    df = _prepare(read_ecotaxa_tsv(path), profile)

    # Volume lookup: portable cache first, then a filesystem scan of the
    # enclosing profile directory if the TSV sits in its _Results tree,
    # then whatever the caller knows about where the TSV came from.
    volumes = load_image_volumes_from_cache_root(cache_root, profile, cruise)
    source, warning = "portable cache", None
    if volumes is None:
        results_dir = path.parent.parent
        if results_dir.name.endswith("_Results"):
            profile_dir = results_dir.parent
            volumes = calculate_profile_volumes(profile_dir, cache_root=cache_root, cruise=cruise)
            source = "profile directory scan"
        elif frame_source is not None:
            found = frame_source(path)
            if found:
                volumes, source, warning = found
        if volumes is None:
            print(f"[VOLUME_CALC] No volumes for {profile}")
            source = None

    return {
        "profiles": [profile],
        "df": df,
        "volumes": {profile: volumes} if volumes else {},
        "volume_source": source,
        "volume_warning": warning,
    }


def load_profiles(profiles_dir: str, profile_names: list,
                  cache_root: str, cruise: str = None) -> dict:
    """Load TSVs for the given server profiles (Dash 'server profiles' flow)."""
    base_dir = Path(profiles_dir)
    all_dfs, volume_dicts = [], {}
    missing = []

    for name in profile_names:
        profile_dir = base_dir / name
        tsv = next((c for c in _profile_tsv_candidates(profile_dir) if c.exists()), None)
        if tsv is None:
            print(f"[LOAD] TSV not found for {name}")
            missing.append(name)
            continue

        df = _prepare(read_ecotaxa_tsv(tsv), name)
        all_dfs.append(df)
        print(f"[LOAD] {name}: {len(df):,} rows")

        vols = calculate_profile_volumes(profile_dir, cache_root=cache_root, cruise=cruise)
        if vols:
            volume_dicts[name] = vols

    if not all_dfs:
        raise ValueError("no valid profiles found")

    combined = pd.concat(all_dfs, ignore_index=True)
    return {
        "profiles": [d["profile"].iloc[0] for d in all_dfs] or list(profile_names),
        "df": combined,
        "volumes": volume_dicts,
        "volume_source": "profile directory scan",
        "missing": missing,
    }


# ─── Binning (identical to the Dash app) ─────────────────────────────────────

def _segments_to_edges(text: str) -> list:
    """Parse 'start:stop:step, ...' into a sorted unique edge list."""
    edges = set()
    for seg in text.split(","):
        seg = seg.strip()
        if not seg:
            continue
        parts = seg.split(":")
        if len(parts) != 3:
            raise ValueError(f"Bad segment '{seg}' — expected start:stop:step")
        start, stop, step = float(parts[0]), float(parts[1]), float(parts[2])
        if step <= 0 or start >= stop:
            raise ValueError(f"Invalid range in '{seg}'")
        val = start
        while val <= stop + 1e-9:
            edges.add(round(val, 6))
            val += step
    return sorted(edges)


def compute_edges(mode: str, preset: str, segments: str, manual: str) -> list:
    """Resolve the requested depth-bin edges. Raises on bad input."""
    if mode == "preset":
        spec = PRESETS.get(preset or "fine", "0:5000:100")
        return spec if isinstance(spec, list) else _segments_to_edges(spec)
    if mode == "manual":
        return sorted(set(float(x.strip()) for x in (manual or "0,5000").split(",")))
    return _segments_to_edges(segments or "0:5000:100")


def bin_dataframe(df: pd.DataFrame, volume_dicts: dict, edges: list) -> pd.DataFrame:
    """Aggregate the raw DataFrame into depth bins.
    Returns a small summary DataFrame ready for plotting."""
    labels = [f"{edges[i]}-{edges[i+1]}m" for i in range(len(edges) - 1)]

    df = df.copy()
    df["depth_bin"] = pd.cut(
        df["object_pressure"], bins=edges, labels=labels,
        include_lowest=True, right=False,
    )

    # Sum volumes per bin across all profiles.
    total_vol_per_bin = {l: 0.0 for l in labels}
    for profile_name, vdict in volume_dicts.items():
        pressures = np.array([float(p) for p in vdict.keys()])
        vols = np.array(list(vdict.values()), dtype=float)
        bin_indices = pd.cut(pressures, bins=edges, labels=False,
                             include_lowest=True, right=False)
        for bin_idx, vol in zip(bin_indices, vols):
            if not pd.isna(bin_idx):
                total_vol_per_bin[labels[int(bin_idx)]] += vol

    # Aggregate biology
    agg_spec = {"count": ("object_annotation_category", "size"),
                "biovolume_mm3": ("biovolume_mm3", "sum")}
    if "object_esd" in df.columns:
        agg_spec["esd_mean_um"]   = ("object_esd", "mean")
        agg_spec["esd_median_um"] = ("object_esd", "median")
        agg_spec["esd_std_um"]    = ("object_esd", "std")
    if "object_pressure" in df.columns:
        agg_spec["pressure_mean_dbar"] = ("object_pressure", "mean")
    if "profile" in df.columns:
        agg_spec["n_profiles"] = ("profile", "nunique")

    binned = (
        df.groupby(["depth_bin", "object_annotation_category"], observed=True)
        .agg(**agg_spec)
        .reset_index()
    )

    binned["depth_bin_str"] = binned["depth_bin"].astype(str)
    binned["sampled_volume_L"] = binned["depth_bin_str"].map(total_vol_per_bin).fillna(0)

    midpoints = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)]
    label_to_mid = dict(zip(labels, midpoints))
    binned["depth_bin_midpoint"] = binned["depth_bin"].map(label_to_mid)

    mask = binned["sampled_volume_L"] > 0
    # No volume is no measurement, not zero concentration: NaN leaves a gap
    # in the plot where a 0 would draw a false minimum.
    binned["concentration_per_L"] = np.where(mask, binned["count"] / binned["sampled_volume_L"], np.nan)
    binned["biovolume_mm3_per_L"] = np.where(mask, binned["biovolume_mm3"] / binned["sampled_volume_L"], np.nan)

    return binned


def esd_spectrum_dataframe(
    df: pd.DataFrame,
    volume_dicts: dict,
    depth_edges: list,
    esd_min: float = 50.0,
    esd_max: float = 10000.0,
    n_bins: int = 25,
) -> pd.DataFrame:
    """Biovolume / L vs ESD (µm) spectrum with one row per
    (depth_bin, esd_bin_center). Uses log-spaced ESD bins."""
    required = {"object_esd", "object_pressure"}
    if not required.issubset(df.columns):
        return pd.DataFrame()

    depth_labels = [
        f"{depth_edges[i]:.0f}–{depth_edges[i+1]:.0f} dbar"
        for i in range(len(depth_edges) - 1)
    ]

    df = df.copy()
    df["depth_bin"] = pd.cut(
        df["object_pressure"], bins=depth_edges, labels=depth_labels,
        include_lowest=True, right=False,
    )
    df = df.dropna(subset=["depth_bin"])

    # --- Total sampled volume per depth bin (same logic as bin_dataframe) ---
    total_vol_per_bin = {lbl: 0.0 for lbl in depth_labels}
    for _profile, vdict in volume_dicts.items():
        pressures = np.array([float(p) for p in vdict.keys()])
        vols = np.array(list(vdict.values()), dtype=float)
        bin_idx = pd.cut(pressures, bins=depth_edges, labels=False,
                         include_lowest=True, right=False)
        for idx, vol in zip(bin_idx, vols):
            if not pd.isna(idx):
                total_vol_per_bin[depth_labels[int(idx)]] += vol

    # --- Log-spaced ESD bins ---
    esd_edges = np.logspace(np.log10(max(esd_min, 1e-6)),
                            np.log10(max(esd_max, esd_min + 1)), n_bins + 1)
    esd_centers = np.sqrt(esd_edges[:-1] * esd_edges[1:])   # geometric mean
    esd_widths = esd_edges[1:] - esd_edges[:-1]             # bin width in µm
    esd_idx_labels = list(range(n_bins))

    df["esd_bin_idx"] = pd.cut(
        df["object_esd"], bins=esd_edges, labels=esd_idx_labels,
        include_lowest=True,
    )
    df = df.dropna(subset=["esd_bin_idx"])
    df["esd_bin_idx"] = df["esd_bin_idx"].astype(int)
    df["esd_center_um"] = df["esd_bin_idx"].map(lambda i: esd_centers[i])

    if "biovolume_mm3" not in df.columns:
        esd_mm = df["object_esd"] / 1000.0
        df["biovolume_mm3"] = (np.pi / 6.0) * esd_mm ** 3

    grouped = (
        df.groupby(["depth_bin", "esd_center_um"], observed=True)
        .agg(
            count=("biovolume_mm3", "size"),
            biovolume_mm3_total=("biovolume_mm3", "sum"),
        )
        .reset_index()
    )
    grouped["total_vol_L"] = grouped["depth_bin"].astype(str).map(total_vol_per_bin).fillna(0)
    grouped = grouped[grouped["total_vol_L"] > 0].copy()
    grouped["biovolume_mm3_per_L"] = grouped["biovolume_mm3_total"] / grouped["total_vol_L"]
    center_to_width = {esd_centers[i]: esd_widths[i] for i in range(n_bins)}
    grouped["esd_bin_width_um"] = grouped["esd_center_um"].map(center_to_width)
    grouped["biovolume_mm3_per_L_per_um"] = (
        grouped["biovolume_mm3_per_L"] / grouped["esd_bin_width_um"]
    )
    grouped["count_per_L"] = grouped["count"] / grouped["total_vol_L"]
    grouped["count_per_L_per_um"] = grouped["count_per_L"] / grouped["esd_bin_width_um"]
    return grouped


# ─── Figure building (the two Dash branches, as plotly.js dicts) ─────────────

# plotly's default qualitative colours, as px.colors.qualitative.Plotly.
PALETTE = ["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
           "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52"]


def _records(df: pd.DataFrame) -> list:
    """JSON-safe list of row dicts (native floats, NaN → None)."""
    return json.loads(df.to_json(orient="records"))


def _values(series: pd.Series) -> list:
    return json.loads(series.to_json(orient="values"))


def _table(df: pd.DataFrame):
    return {"data": _records(df), "columns": [{"name": c, "id": c} for c in df.columns]}


def _empty(title: str):
    return {"figure": {"data": [], "layout": {"title": {"text": title}}},
            "table": _table(pd.DataFrame()), "points": 0, "status": title}


def _axis(title: str, log: bool = False, reversed_: bool = False) -> dict:
    axis = {"title": {"text": title}, "type": "log" if log else "linear"}
    if reversed_:
        axis["autorange"] = "reversed"
    return axis


def render_plot(df: pd.DataFrame, volume_dicts: dict, edges: list, plot_type: str,
                x_axis: str = None, y_axis: str = None, color_col: str = None,
                plot_options: list = None, taxon_filter: list = None,
                validated_only: bool = False,
                esd_min: float = 50.0, esd_max: float = 10000.0,
                esd_nbins: int = 25, esd_yaxis: str = "biovolume_mm3_per_L") -> dict:
    """Build the main figure + data preview for the current selection.
    Mirrors the Dash update_dashboard callback."""
    plot_options = plot_options or []

    # ── validated-only filter ──
    if validated_only and "object_annotation_status" in df.columns:
        df = df[df["object_annotation_status"] == "validated"]

    # ── taxon filter ──
    if taxon_filter and "object_annotation_category" in df.columns:
        df = df[df["object_annotation_category"].isin(taxon_filter)]

    print(f"[PLOT] {len(df):,} rows after filters, plot_type={plot_type}")

    if not edges or len(edges) < 2:
        edges = [0, 100, 1000, 5000]

    # ══════════════════════════════════════════════════════════════════════
    # ESD SPECTRUM branch
    # ══════════════════════════════════════════════════════════════════════
    if plot_type == "esd":
        try:
            spec_df = esd_spectrum_dataframe(df, volume_dicts, edges,
                                             esd_min, esd_max, esd_nbins)
        except Exception as e:
            traceback.print_exc()
            return _empty(f"ESD spectrum error: {e}")

        if spec_df.empty:
            return _empty("No data for ESD spectrum (check bins / data)")

        depth_bins = spec_df["depth_bin"].unique().tolist()
        mode = "lines+markers" if "lines" in plot_options else "lines"

        _y_meta = {
            "biovolume_mm3_per_L":        ("Biovolume (mm³ L⁻¹)",            "Biovolume/L"),
            "biovolume_mm3_per_L_per_um": ("Biovolume norm. (mm³ L⁻¹ µm⁻¹)", "Norm. biovolume"),
            "count_per_L":                ("Abundance (# L⁻¹)",              "Abundance/L"),
            "count_per_L_per_um":         ("Abundance norm. (# L⁻¹ µm⁻¹)",   "Norm. abundance"),
        }
        y_col = esd_yaxis if esd_yaxis in _y_meta else "biovolume_mm3_per_L"
        y_label, y_hover_label = _y_meta[y_col]

        traces = []
        for i, dbin in enumerate(depth_bins):
            sub = spec_df[spec_df["depth_bin"] == dbin].sort_values("esd_center_um")
            total_count = int(sub["count"].sum())
            color = PALETTE[i % len(PALETTE)]
            traces.append({
                "type": "scatter",
                "x": _values(sub["esd_center_um"]),
                "y": _values(sub[y_col]),
                "name": f"{dbin}  (n={total_count:,})",
                "mode": mode,
                "line": {"color": color},
                "marker": {"color": color, "size": 5},
                "customdata": json.loads(
                    sub[["count", "biovolume_mm3_total", "total_vol_L"]].to_json(orient="values")),
                "hovertemplate": (
                    f"ESD: %{{x:.1f}} µm<br>"
                    f"{y_hover_label}: %{{y:.4e}}<br>"
                    "Count: %{customdata[0]:.0f}<br>"
                    "Total biovolume: %{customdata[1]:.4e} mm³<br>"
                    "Sampled vol: %{customdata[2]:.2f} L"
                    "<extra>%{fullData.name}</extra>"
                ),
            })

        layout = {
            "xaxis": _axis("ESD (µm)", "log_x" in plot_options),
            "yaxis": _axis(y_label, "log_y" in plot_options),
            "legend": {"title": {"text": "Depth bin"}},
        }
        return {"figure": {"data": traces, "layout": layout},
                "table": _table(spec_df.drop(columns=["depth_bin"])),
                "points": len(spec_df), "status": ""}

    # ══════════════════════════════════════════════════════════════════════
    # DEPTH PROFILE branch (binned)
    # ══════════════════════════════════════════════════════════════════════
    try:
        plot_df = bin_dataframe(df, volume_dicts, edges)
    except Exception as e:
        traceback.print_exc()
        return _empty(f"Binning error: {e}")
    if plot_df.empty:
        return _empty("No data after binning (check bin edges)")

    # depth_bin is the categorical that depth_bin_str spells out.
    plot_df = plot_df.drop(columns=["depth_bin"])
    cols = list(plot_df.columns)
    if x_axis not in cols:
        x_axis = cols[0]
    if y_axis not in cols:
        y_axis = cols[0]

    mode = "lines+markers" if "lines" in plot_options else "markers"
    hovertemplate = "<br>".join(
        f"{c}: %{{customdata[{j}]}}" for j, c in enumerate(cols)) + "<extra></extra>"

    if color_col and color_col in plot_df.columns:
        groups = list(plot_df.groupby(color_col, sort=True))
    else:
        groups = [("", plot_df)]

    traces = []
    for i, (key, sub) in enumerate(groups):
        color = PALETTE[i % len(PALETTE)]
        traces.append({
            "type": "scatter",
            "x": _values(sub[x_axis]),
            "y": _values(sub[y_axis]),
            "name": str(key),
            "mode": mode,
            "line": {"color": color},
            "marker": {"color": color},
            "customdata": json.loads(sub[cols].to_json(orient="values")),
            "hovertemplate": hovertemplate,
        })

    invert_y = bool(y_axis) and ("depth" in y_axis.lower() or "pressure" in y_axis.lower())
    layout = {
        "xaxis": _axis(x_axis, "log_x" in plot_options),
        "yaxis": _axis(y_axis, "log_y" in plot_options, reversed_=invert_y),
        "showlegend": len(groups) > 1,
        "legend": {"title": {"text": color_col or ""}},
    }

    print(f"[PLOT] Done — {len(plot_df)} points rendered")
    return {"figure": {"data": traces, "layout": layout}, "table": _table(plot_df),
            "points": len(plot_df), "status": ""}
