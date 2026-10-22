"""Tiny software renderer: STL triangles -> shaded PNG with numpy + matplotlib (runs in the server process).

Why not render in FreeCAD? Headless FreeCAD has no 3D view. FreeCAD exports STL, we project the triangles
orthographically, cull back faces, sort by depth (painter's algorithm) and apply simple 3-light Lambert shading.
"""
import io, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection

from cad_mcp.materials import MATERIALS

PALETTE = [(0.55, 0.66, 0.82), (0.82, 0.62, 0.45), (0.60, 0.75, 0.58), (0.78, 0.58, 0.72), (0.85, 0.80, 0.50), (0.55, 0.78, 0.80)]
VIEWS = ("iso", "front", "back", "top", "bottom", "right", "left", "grid")


def load_stl(path):
    """Read a binary or ASCII STL into an (n,3,3) array of triangle vertices."""
    raw = open(path, "rb").read()
    if raw[:5] == b"solid" and b"facet" in raw[:400]:
        v = [list(map(float, ln.split()[1:4])) for ln in raw.decode("ascii", "ignore").splitlines() if ln.strip().startswith("vertex")]
        return np.array(v, float).reshape(-1, 3, 3)
    n = int(np.frombuffer(raw[80:84], "<u4")[0])
    dt = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    return np.frombuffer(raw[84:84 + 50 * n], dt)["v"].astype(float)


def view_basis(view):
    """(right, up, toward_viewer) unit vectors in world coordinates (Z up)."""
    t = {"iso": (1, -1, 0.9), "front": (0, -1, 0), "back": (0, 1, 0), "top": (0, 0, 1), "bottom": (0, 0, -1),
         "right": (1, 0, 0), "left": (-1, 0, 0)}[view]
    t = np.array(t, float); t /= np.linalg.norm(t)
    if abs(t[2]) > 0.99:
        r = np.array([1.0, 0, 0]); u = np.cross(t, r)
    else:
        r = np.cross([0, 0, 1.0], t); r /= np.linalg.norm(r); u = np.cross(t, r)
    return r, u, t


def _color(item, i):
    c = item.get("color")
    if c:
        return tuple(float(x) for x in str(c).split(","))
    m = item.get("material")
    if m in MATERIALS:
        return MATERIALS[m]["color"]
    return PALETTE[i % len(PALETTE)]


def _shade(n, t, r, u):
    """Three lights fixed relative to the camera: key (upper left), fill (right), plus ambient. Clipped to avoid blow-out."""
    key = -0.55 * r + 0.65 * u + 0.52 * t
    fill = 0.7 * r + 0.1 * u + 0.4 * t
    key /= np.linalg.norm(key); fill /= np.linalg.norm(fill)
    s = 0.30 + 0.55 * np.clip(n @ key, 0, 1) + 0.18 * np.clip(n @ fill, 0, 1)
    return np.clip(s, 0, 0.97)


def apply_explode(items, factor):
    if not factor:
        return items
    allv = np.concatenate([it["tris"].reshape(-1, 3) for it in items])
    c0 = (allv.min(0) + allv.max(0)) / 2
    out = []
    for it in items:
        v = it["tris"].reshape(-1, 3)
        ci = (v.min(0) + v.max(0)) / 2
        out.append(dict(it, tris=it["tris"] + (ci - c0) * factor))
    return out


def _feature_edges(tr, r, u, t, angle_deg=35.0):
    """Edges whose two triangles meet at more than angle_deg (or that have only one triangle). Returns 2D segments and sort depth."""
    if len(tr) == 0:
        return [], []
    n = np.cross(tr[:, 1] - tr[:, 0], tr[:, 2] - tr[:, 0])
    ln = np.linalg.norm(n, axis=1); ln[ln == 0] = 1; n = n / ln[:, None]
    scale = max(np.ptp(tr.reshape(-1, 3), axis=0).max(), 1e-9)
    key = np.round(tr / (scale * 1e-6)).astype(np.int64)
    vid = {}
    ids = np.array([[vid.setdefault(tuple(v), len(vid)) for v in tri] for tri in key])
    edges = {}
    for ti in range(len(tr)):
        for a, b in ((0, 1), (1, 2), (2, 0)):
            i, j = ids[ti, a], ids[ti, b]
            edges.setdefault((min(i, j), max(i, j)), []).append((ti, a, b))
    coslim = np.cos(np.radians(angle_deg))
    segs, deps = [], []
    for k, lst in edges.items():
        if len(lst) == 2 and n[lst[0][0]] @ n[lst[1][0]] > coslim:
            continue
        if not any(n[ti] @ t > -1e-6 for ti, _, _ in lst):
            continue
        ti, a, b = lst[0]
        p0, p1 = tr[ti, a], tr[ti, b]
        segs.append((np.array([p0 @ r, p0 @ u]), np.array([p1 @ r, p1 @ u])))
        deps.append(max(p0 @ t, p1 @ t) + 1e-3 * scale)
    return segs, deps


def _draw(ax, items, view, label=None):
    r, u, t = view_basis(view)
    polys, cols, depth = [], [], []
    allp = []
    for i, it in enumerate(items):
        tr = it["tris"]
        if len(tr) == 0:
            continue
        n = np.cross(tr[:, 1] - tr[:, 0], tr[:, 2] - tr[:, 0])
        ln = np.linalg.norm(n, axis=1); ok = ln > 1e-12
        n[ok] /= ln[ok, None]
        front = (n @ t) > -1e-6 if it.get("cull", True) else np.ones(len(tr), bool)
        base = np.array(_color(it, i))
        sh = _shade(n, t, r, u)
        if "vals" in it:                      # scalar field per triangle (FEM plots): colormap instead of material colour
            cm = plt.get_cmap("turbo"); vm = it["vmax"]
            fc = np.array([cm(min(v / vm, 1.0))[:3] for v in it["vals"]])
            sh = 0.75 + 0.25 * sh
        sh = np.where(n @ t < 0, sh * 0.6, sh)
        xy = np.stack([tr @ r, tr @ u], axis=-1)
        allp.append(xy.reshape(-1, 2))
        d = (tr @ t).mean(1)
        sel = front & ok
        polys += list(xy[sel]); depth += list(d[sel])
        if "vals" in it:
            cols += list(np.clip(fc[sel] * sh[sel, None], 0, 1))
        else:
            cols += list(np.clip(base[None, :] * sh[sel, None, ] * np.array([1.12, 1.08, 1.0]), 0, 1))
    # --- feature edges (sharp creases + open boundaries) as thin dark quads, depth-sorted together with the faces
    allxy = np.concatenate(allp); span = (allxy.max(0) - allxy.min(0)).max()
    w = span * 0.0016
    for it in items:
        e, dep = _feature_edges(it["tris"], r, u, t)
        for (a, b), dz in zip(e, dep):
            d2 = b - a; L = np.hypot(*d2)
            if L < 1e-9:
                continue
            nrm = np.array([-d2[1], d2[0]]) / L * w
            polys.append(np.array([a + nrm, b + nrm, b - nrm, a - nrm])); cols.append((0.12, 0.12, 0.14)); depth.append(dz)
    order = np.argsort(depth)
    pc = PolyCollection([polys[k] for k in order], facecolors=[cols[k] for k in order],
                        edgecolors=[cols[k] for k in order], linewidths=0.6, antialiased=False)
    ax.add_collection(pc)
    p = np.concatenate(allp)
    lo, hi = p.min(0), p.max(0); c = (lo + hi) / 2; h = max((hi - lo).max() / 2, 1e-6) * 1.12
    ax.set_xlim(c[0] - h, c[0] + h); ax.set_ylim(c[1] - h, c[1] + h)
    ax.set_aspect("equal"); ax.axis("off")
    ax.text(0.02, 0.97, label or view, transform=ax.transAxes, fontsize=9, color="#444", va="top")
    ax.text(0.98, 0.03, f"{2*h/1.12:.0f} mm span", transform=ax.transAxes, fontsize=7, color="#888", ha="right")


def render_png(items, view="iso", size=900, explode=0.0, title=None):
    """items: [{name, tris(n,3,3), color|material}] -> PNG bytes."""
    items = apply_explode(items, explode)
    bg = "#f1f2f4"
    if view == "grid":
        fig, axs = plt.subplots(2, 2, figsize=(size / 100, size / 100), dpi=100, facecolor=bg)
        for ax, v in zip(axs.flat, ("iso", "front", "top", "right")):
            ax.set_facecolor(bg); _draw(ax, items, v)
    else:
        fig = plt.figure(figsize=(size / 100, size / 100 * 0.8), dpi=100, facecolor=bg)
        ax = fig.add_axes([0.02, 0.02, 0.96, 0.96]); ax.set_facecolor(bg)
        _draw(ax, items, view)
    if title:
        fig.suptitle(title, fontsize=10, color="#333")
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=bg); plt.close(fig)
    return buf.getvalue()


def render_field_png(tris, vals, vmax, view="iso", size=900, title="", unit="MPa"):
    """Colour-mapped surface plot (FEM results). vals: one scalar per triangle."""
    bg = "#f1f2f4"
    fig = plt.figure(figsize=(size / 100 * 1.1, size / 100 * 0.8), dpi=100, facecolor=bg)
    ax = fig.add_axes([0.02, 0.02, 0.80, 0.94]); ax.set_facecolor(bg)
    _draw(ax, [dict(tris=tris, vals=vals, vmax=vmax)], view)
    cax = fig.add_axes([0.86, 0.15, 0.025, 0.65])
    sm = plt.cm.ScalarMappable(cmap="turbo", norm=plt.Normalize(0, vmax)); sm.set_array([])
    fig.colorbar(sm, cax=cax, label=unit)
    fig.suptitle(title, fontsize=10, color="#333")
    buf = io.BytesIO(); fig.savefig(buf, format="png", facecolor=bg); plt.close(fig)
    return buf.getvalue()
