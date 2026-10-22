"""Documentation outputs: TechDraw-style drawings (HLR projection via TechDraw.projectEx) and the BOM."""
import csv, math, os, sys, time
import numpy as np
import FreeCAD as App
import Part, TechDraw
from FreeCAD import Vector as V

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cad_mcp.core import op, pick, get, meta
from cad_mcp.materials import MATERIALS, get_material
import render as RD

SHEETS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}


def _project(shape, basis):
    r, u, t = (np.array(b, float) for b in basis)
    m = App.Matrix(r[0], r[1], r[2], 0, u[0], u[1], u[2], 0, t[0], t[1], t[2], 0, 0, 0, 0, 1)
    s = shape.copy(); s.transformShape(m)
    pr = TechDraw.projectEx(s, V(0, 0, 1))
    def poly(comps):
        out = []
        for c in comps:
            if c is None:
                continue
            for e in c.Edges:
                try:
                    pts = e.discretize(Deflection=0.05)
                except Exception:
                    continue
                out.append(np.array([[p.x, p.y] for p in pts]))
        return out
    return dict(vis=poly(pr[0:4]), hid=poly(pr[5:7]), shape=s)


def _bounds(polys):
    a = np.concatenate([p for p in polys])
    return a.min(0), a.max(0)


def _dim_h(ax, x0, x1, y, text, fs=6.5):
    ax.annotate("", xy=(x0, y), xytext=(x1, y), arrowprops=dict(arrowstyle="<->", lw=0.5, color="k", shrinkA=0, shrinkB=0))
    ax.text((x0 + x1) / 2, y + 1.2, text, ha="center", va="bottom", fontsize=fs)


def _dim_v(ax, x, y0, y1, text, fs=6.5):
    ax.annotate("", xy=(x, y0), xytext=(x, y1), arrowprops=dict(arrowstyle="<->", lw=0.5, color="k", shrinkA=0, shrinkB=0))
    ax.text(x - 1.2, (y0 + y1) / 2, text, ha="right", va="center", rotation=90, fontsize=fs)


@op("make_drawing", mutates=False)
def make_drawing(doc, out_base, objects=None, title=None, sheet="A3", author="", scale=None, hidden=True, dimension_holes=True):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    objs = pick(doc, objects)
    shapes = [o.Shape for o in objs]
    comp = Part.makeCompound(shapes)
    try:
        comp = comp.removeSplitter() if len(shapes) == 1 else comp
    except Exception:
        pass
    W, H = SHEETS[sheet]
    views = {"front": RD.view_basis("front"), "top": RD.view_basis("top"), "right": RD.view_basis("right"), "iso": RD.view_basis("iso")}
    proj = {k: _project(comp, b) for k, b in views.items()}
    bb = {k: _bounds(v["vis"] + v["hid"]) for k, v in proj.items()}
    ext = {k: bb[k][1] - bb[k][0] for k in bb}
    # third-angle layout: top above front, right to the right of front, iso top-right
    gap = 34.0
    wcol0 = max(ext["front"][0], ext["top"][0]); wcol1 = max(ext["right"][0], ext["iso"][0])
    hrow0 = max(ext["top"][1], ext["iso"][1]); hrow1 = ext["front"][1]
    availw, availh = W - 20 - 3 * gap - 20, H - 28 - 2 * gap - 40
    sc = scale or min(availw / (wcol0 + wcol1), availh / (hrow0 + hrow1))
    if not scale:
        sc = max([c for c in (20, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05, 0.02) if c <= sc] or [0.02])
    fig = plt.figure(figsize=(W / 25.4, H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.add_patch(plt.Rectangle((10, 8), W - 20, H - 16, fill=False, lw=1.2, ec="k"))
    ox = 10 + gap + 12; oy_top = H - 28
    total_w = wcol0 * sc + gap + wcol1 * sc
    ox = 10 + (W - 20 - total_w) / 2
    pos = {}
    pos["top"] = (ox, oy_top - hrow0 * sc)
    pos["iso"] = (ox + wcol0 * sc + gap, oy_top - hrow0 * sc)
    pos["front"] = (ox, oy_top - hrow0 * sc - gap - hrow1 * sc)
    pos["right"] = (ox + wcol0 * sc + gap, oy_top - hrow0 * sc - gap - hrow1 * sc)
    holes = []
    for k, (px, py) in pos.items():
        lo, hi = bb[k]
        sh = lambda p: (p - lo) * sc + np.array([px, py])
        if hidden and k != "iso":
            ax.add_collection(LineCollection([sh(p) for p in proj[k]["hid"]], colors="0.35", linewidths=0.35, linestyles=(0, (3, 2))))
        ax.add_collection(LineCollection([sh(p) for p in proj[k]["vis"]], colors="k", linewidths=0.7))
        ax.text(px, py + ext[k][1] * sc + 4, k.upper() if k != "iso" else "ISOMETRIC", fontsize=7, fontweight="bold")
        if k != "iso":
            _dim_h(ax, px, px + ext[k][0] * sc, py - 7, f"{ext[k][0]:.1f}")
            _dim_v(ax, px - 7, py, py + ext[k][1] * sc, f"{ext[k][1]:.1f}")
            if dimension_holes and k in ("top", "front", "right"):
                # circles whose axis is parallel to the view direction appear as true circles
                r_, u_, t_ = (np.array(b) for b in views[k])
                seen = set()
                for e in proj[k]["shape"].Edges:
                    if e.Curve.TypeId == "Part::GeomCircle" and abs(abs(e.Curve.Axis.z) - 1) < 1e-6 and e.isClosed():
                        key = (round(e.Curve.Center.x, 1), round(e.Curve.Center.y, 1), round(e.Curve.Radius, 2))
                        if key in seen:
                            continue
                        seen.add(key)
                        c = sh(np.array([e.Curve.Center.x, e.Curve.Center.y]))
                        rr = e.Curve.Radius * sc
                        ax.plot([c[0] - rr * 1.5, c[0] + rr * 1.5], [c[1], c[1]], lw=0.3, c="k", ls="-.")
                        ax.plot([c[0], c[0]], [c[1] - rr * 1.5, c[1] + rr * 1.5], lw=0.3, c="k", ls="-.")
                        a = math.radians(45)
                        ax.annotate(f"⌀{2*e.Curve.Radius:.1f}", xy=(c[0] + rr * math.cos(a), c[1] + rr * math.sin(a)),
                                    xytext=(c[0] + rr * 2.2 + 3, c[1] + rr * 2.2 + 3 + 6 * len([h for h in holes if h[:2] == key[:2]])), fontsize=6.5,
                                    arrowprops=dict(arrowstyle="-", lw=0.4, color="k"))
                        holes.append(key)
    # title block
    mass = 0.0; mats = set()
    for o in objs:
        mt = meta(o)
        if mt["material"]:
            mats.add(mt["material"]); mass += o.Shape.Volume * MATERIALS[mt["material"]]["density"] * 1e-6
    bx, by, bw, bh = W - 10 - 140, 8, 140, 32
    ax.add_patch(plt.Rectangle((bx, by), bw, bh, fill=False, lw=0.9, ec="k"))
    ax.plot([bx, bx + bw], [by + 20, by + 20], lw=0.5, c="k"); ax.plot([bx + 70, bx + 70], [by, by + 20], lw=0.5, c="k")
    ax.text(bx + 3, by + 26, title or (os.path.basename(out_base)), fontsize=11, fontweight="bold", va="center")
    ax.text(bx + 3, by + 15, "Material: " + (", ".join(sorted(mats)) or "n/a"), fontsize=6.5, va="center")
    ax.text(bx + 3, by + 9, f"Mass: {mass:.1f} g" if mass else "Mass: n/a", fontsize=6.5, va="center")
    ax.text(bx + 3, by + 3.5, f"Parts: {len(objs)}   Units: mm", fontsize=6.5, va="center")
    ax.text(bx + 73, by + 15, f"Scale: {sc:g}:1" if sc >= 1 else f"Scale: 1:{1/sc:g}", fontsize=6.5, va="center")
    ax.text(bx + 73, by + 9, "Third-angle projection", fontsize=6.5, va="center")
    ax.text(bx + 73, by + 3.5, f"{author}  {time.strftime('%Y-%m-%d')}", fontsize=6.5, va="center")
    ax.text(12, 11, "General tolerance ISO 2768-m", fontsize=6, color="0.3")
    os.makedirs(os.path.dirname(out_base), exist_ok=True)
    fig.savefig(out_base + ".svg", format="svg"); fig.savefig(out_base + ".pdf", format="pdf"); fig.savefig(out_base + ".png", dpi=110)
    plt.close(fig)
    return dict(svg=out_base + ".svg", pdf=out_base + ".pdf", png=out_base + ".png", sheet=sheet, scale=sc, hole_dimensions=len(holes),
                overall={k: [round(float(x), 3) for x in ext[k]] for k in ("front", "top", "right")})


@op("bom", mutates=False)
def bom(doc, out_base, objects=None):
    rows = {}
    for o in pick(doc, objects):
        m = meta(o)
        key = (m["part_no"] or o.Name, m["material"], m["description"])
        vol = o.Shape.Volume
        mass = vol * MATERIALS[m["material"]]["density"] * 1e-6 if m["material"] else None
        r = rows.setdefault(key, dict(part_no=key[0], name=o.Name, description=m["description"], material=m["material"] or "",
                                      supplier=m["supplier"], quantity=0, volume_mm3=round(vol, 2),
                                      unit_mass_g=None if mass is None else round(mass, 3)))
        r["quantity"] += max(int(m["quantity"]), 1)
    items = list(rows.values())
    for i, r in enumerate(items, 1):
        r["item"] = i
        r["total_mass_g"] = None if r["unit_mass_g"] is None else round(r["unit_mass_g"] * r["quantity"], 3)
    total = round(sum(r["total_mass_g"] or 0 for r in items), 3)
    os.makedirs(os.path.dirname(out_base), exist_ok=True)
    cols = ["item", "part_no", "name", "description", "material", "supplier", "quantity", "volume_mm3", "unit_mass_g", "total_mass_g"]
    with open(out_base + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); [w.writerow({c: r[c] for c in cols}) for r in items]
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join("" if r[c] is None else str(r[c]) for c in cols) + " |" for r in items]
    md.append(f"\nTotal mass: {total} g")
    open(out_base + ".md", "w").write("\n".join(md) + "\n")
    return dict(csv=out_base + ".csv", markdown=out_base + ".md", items=items, total_mass_g=total, line_items=len(items),
                total_parts=sum(r["quantity"] for r in items))
