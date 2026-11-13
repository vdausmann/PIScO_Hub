"""Backscatter profile from LADCP PD0 files: where the scattering layers are.

    python -m scripts.ladcp_backscatter FILE [FILE ...] [--max-depth M] [--png OUT.png]

Each ensemble's echo intensity is turned into a relative volume
backscatter, Sv' = Kc * EI + 20 log10(R) + 2 alpha R (Deines 1999,
without the instrument constants: fine for finding layers, not for
absolute Sv), and every depth cell is placed at an absolute depth: the
package depth, integrated from the vertical velocity (pd0.vertical_velocity),
minus the cell's range for an up-looker or plus it for a down-looker.
--max-depth rescales that integral so its deepest point matches the
cast's real maximum depth (from the CTD), which removes most of the
drift of the integration.

The result is a profile of median Sv' in depth bins over the whole cast,
with the layers picked as maxima that stand clear of their surroundings.
Noise-floor cells (EI near the minimum, beyond the useful range) are
left out.
"""

import argparse
import math
import sys

import numpy as np

from app.backend.ladcp import pd0

KC = 0.45              # dB per count, WorkHorse typical
ALPHA = 0.07 / 1.0     # dB/m one-way absorption at 300 kHz, seawater (~0.06-0.08)
NOISE_MARGIN = 8       # counts above the per-ensemble noise floor to keep a cell


def ensembles(paths):
    for path in paths:
        for _, ens in pd0.iter_file(path):
            if ens.get("ei") is not None and ens.get("time") is not None:
                yield ens


def package_depth(enss):
    """Depth of the package per ensemble from the integrated vertical
    velocity, starting at 0."""
    depth, prev, out = 0.0, None, []
    for e in enss:
        w = pd0.vertical_velocity(e)
        t = e["time"]
        if w is not None and prev is not None and 0 < t - prev < 60:
            depth += w * (t - prev)
        prev = t
        out.append(depth)
    return np.array(out)


def profile(paths, max_depth=None, bin_m=10.0):
    enss = list(ensembles(paths))
    if not enss:
        raise SystemExit("no ensembles with echo intensity in " + ", ".join(paths))
    z_pkg = package_depth(enss)
    scale = 1.0
    if max_depth and z_pkg.max() > 0:
        scale = max_depth / z_pkg.max()
        z_pkg = z_pkg * scale
    depths, svs = [], []
    for e, zp in zip(enss, z_pkg):
        ei = e["ei"].astype(float).mean(axis=1)               # mean over beams
        rng = e["bin1_m"] + e["cell_m"] * np.arange(len(ei))
        rng_slant = rng / math.cos(math.radians(e.get("beam_angle", 20)))
        floor = np.percentile(ei, 10)
        keep = ei > floor + NOISE_MARGIN
        sv = KC * ei + 20 * np.log10(rng_slant) + 2 * ALPHA * rng_slant
        z = zp - rng if e.get("up") else zp + rng
        depths.append(z[keep])
        svs.append(sv[keep])
    z = np.concatenate(depths)
    sv = np.concatenate(svs)
    ok = z > 5                                                   # leave the surface echo out
    z, sv = z[ok], sv[ok]
    if z.size == 0:
        raise SystemExit("no cell stands above the noise floor in any ensemble: "
                         "no echoes to work with (recorded in air?)")
    edges = np.arange(0, z.max() + bin_m, bin_m)
    idx = np.digitize(z, edges) - 1
    med = np.full(len(edges) - 1, np.nan)
    n = np.zeros(len(edges) - 1, int)
    for i in range(len(med)):
        sel = sv[idx == i]
        n[i] = sel.size
        if sel.size >= 5:
            med[i] = np.median(sel)
    centres = edges[:-1] + bin_m / 2
    return {"depth": centres, "sv": med, "n": n, "ensembles": len(enss), "scale": scale,
            "max_package_depth": float(z_pkg.max()), "up": bool(enss[0].get("up")),
            "serial": enss[0].get("serial"),
            "start": enss[0]["time"], "end": enss[-1]["time"]}


def layers(depth, sv, min_prominence_db=2.0, smooth=3):
    """Depths of backscatter maxima that stand out from their surroundings."""
    good = np.isfinite(sv)
    d, s = depth[good], sv[good]
    if s.size < 5:
        return []
    k = np.ones(smooth) / smooth
    ss = np.convolve(s, k, mode="same")
    found = []
    for i in range(1, len(ss) - 1):
        if ss[i] >= ss[i - 1] and ss[i] >= ss[i + 1]:
            window = slice(max(0, i - 10), min(len(ss), i + 11))     # +-100 m at 10 m bins
            base = np.percentile(ss[window], 20)
            prominence = ss[i] - base
            if prominence >= min_prominence_db:
                found.append({"depth": float(d[i]), "prominence_db": round(float(prominence), 1)})
    # keep the strongest within 50 m of each other
    found.sort(key=lambda f: -f["prominence_db"])
    kept = []
    for f in found:
        if all(abs(f["depth"] - k["depth"]) > 50 for k in kept):
            kept.append(f)
    return sorted(kept, key=lambda f: f["depth"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--max-depth", type=float, help="the cast's real maximum depth, m")
    ap.add_argument("--bin", type=float, default=10.0, help="depth bin, m")
    ap.add_argument("--png", help="write a plot here")
    a = ap.parse_args()
    p = profile(a.files, a.max_depth, a.bin)
    from datetime import datetime, timezone
    ts = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M")
    print(f"S/N {p['serial']} ({'UP' if p['up'] else 'DOWN'}), {p['ensembles']} ensembles, "
          f"{ts(p['start'])} - {ts(p['end'])} UTC")
    print(f"package depth from w: max {p['max_package_depth']:.0f} m"
          + (f" (scaled x{p['scale']:.2f} to --max-depth)" if a.max_depth else " (unscaled)"))
    print(f"{'depth m':>8} {'Sv rel dB':>9} {'cells':>6}")
    for z, s, n in zip(p["depth"], p["sv"], p["n"]):
        if np.isfinite(s):
            print(f"{z:8.0f} {s:9.1f} {n:6d}")
    print("layers (backscatter maxima):")
    for f in layers(p["depth"], p["sv"]):
        print(f"  {f['depth']:6.0f} m  (+{f['prominence_db']} dB over the surroundings)")
    if a.png:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(4, 7))
        ax.plot(p["sv"], p["depth"], color="#1f77b4")
        for f in layers(p["depth"], p["sv"]):
            ax.axhline(f["depth"], color="#d62728", lw=0.8, ls="--")
            ax.text(ax.get_xlim()[0], f["depth"], f" {f['depth']:.0f} m", va="bottom", fontsize=8, color="#d62728")
        ax.invert_yaxis()
        ax.set_xlabel("relative Sv (dB)")
        ax.set_ylabel("depth (m)")
        ax.set_title(f"LADCP S/N {p['serial']} {'UP' if p['up'] else 'DOWN'}", fontsize=9)
        fig.tight_layout()
        fig.savefig(a.png, dpi=130)
        print("plot:", a.png)


if __name__ == "__main__":
    sys.exit(main())
