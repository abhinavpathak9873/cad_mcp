"""Assembly, fasteners, clearance, section export and DXF operations."""
import math, os
import FreeCAD as App
import Part
from FreeCAD import Vector as V

from cad_mcp.core import op, get, pick, add_shape, describe, vec, r3, resolve_faces, com, meta


def _face_n(shape, i):
    f = shape.Faces[i - 1]
    if f.Surface.TypeId == "Part::GeomPlane":
        n = f.normalAt(*f.Surface.parameter(f.CenterOfMass)); n.normalize()
        return f, n, f.CenterOfMass
    if f.Surface.TypeId == "Part::GeomCylinder":
        a = f.Surface.Axis; a.normalize()
        return f, a, f.Surface.Center
    raise ValueError("face must be planar or cylindrical")


def _one(shape, spec):
    ids = resolve_faces(shape, spec)
    if len(ids) != 1:
        raise ValueError(f"face spec {spec!r} matched {len(ids)} faces; need exactly 1 (use list_faces)")
    return ids[0]


def _rot_between(a, b):
    """Rotation taking unit vector a to unit vector b."""
    c = a.cross(b)
    if c.Length < 1e-9:
        if a.dot(b) > 0:
            return App.Rotation()
        perp = a.cross(V(1, 0, 0))
        if perp.Length < 1e-6:
            perp = a.cross(V(0, 1, 0))
        return App.Rotation(perp, 180)
    return App.Rotation(c, math.degrees(math.acos(max(-1.0, min(1.0, a.dot(b))))))


@op("mate_faces")
def mate_faces(doc, moving, moving_face, fixed, fixed_face, offset=0.0, rotate_about_normal_deg=0.0):
    """Planar face of `moving` is brought against planar face of `fixed` (normals opposed), centres aligned, gap `offset`."""
    m, f = get(doc, moving), get(doc, fixed)
    im, jf = _one(m.Shape, moving_face), _one(f.Shape, fixed_face)
    _, nm, cm = _face_n(m.Shape, im); _, nf, cf = _face_n(f.Shape, jf)
    s = m.Shape.copy()
    rot = _rot_between(nm, nf * -1)
    s.rotate(cm, rot.Axis, math.degrees(rot.Angle)) if rot.Angle > 1e-9 else None
    _, nm2, cm2 = _face_n(s, im)
    if rotate_about_normal_deg:
        s.rotate(cm2, nf, float(rotate_about_normal_deg))
    _, nm3, cm3 = _face_n(s, im)
    s.translate(cf + nf * float(offset) - cm3)
    m.Shape = s
    return dict(describe(m), moved=moving)


@op("mate_coaxial")
def mate_coaxial(doc, moving, moving_face, fixed, fixed_face, axial_offset=0.0, flip=False):
    """Cylindrical face of `moving` is made coaxial with a cylindrical face of `fixed` (bolt into hole); axial_offset slides along the axis."""
    m, f = get(doc, moving), get(doc, fixed)
    im, jf = _one(m.Shape, moving_face), _one(f.Shape, fixed_face)
    fm, am, _ = _face_n(m.Shape, im); ff, af, _ = _face_n(f.Shape, jf)
    if fm.Surface.TypeId != "Part::GeomCylinder" or ff.Surface.TypeId != "Part::GeomCylinder":
        raise ValueError("both faces must be cylindrical")
    s = m.Shape.copy()
    target = af * (-1 if flip else 1)
    rot = _rot_between(am, target)
    c0 = fm.Surface.Center
    if rot.Angle > 1e-9:
        s.rotate(c0, rot.Axis, math.degrees(rot.Angle))
    fm2 = s.Faces[im - 1]
    p = fm2.Surface.Center
    # shift so that the moving axis passes through the fixed axis (perpendicular components only), then slide
    d = ff.Surface.Center - p
    perp = d - af * d.dot(af)
    s.translate(perp + af * float(axial_offset))
    m.Shape = s
    return dict(describe(m), moved=moving)


# ----------------------------------------------------------------------------- fasteners (parametric ISO-style)
# size: (dk head dia, k head height, s socket AF, nut AF s, nut m, washer d1, d2, h)
FAST = {"M3": (5.5, 3.0, 2.5, 5.5, 2.4, 3.2, 7.0, 0.5), "M4": (7.0, 4.0, 3.0, 7.0, 3.2, 4.3, 9.0, 0.8),
        "M5": (8.5, 5.0, 4.0, 8.0, 4.7, 5.3, 10.0, 1.0), "M6": (10.0, 6.0, 5.0, 10.0, 5.2, 6.4, 12.0, 1.6),
        "M8": (13.0, 8.0, 6.0, 13.0, 6.8, 8.4, 16.0, 1.6), "M10": (16.0, 10.0, 8.0, 16.0, 8.4, 10.5, 20.0, 2.0),
        "M12": (18.0, 12.0, 10.0, 18.0, 10.8, 13.0, 24.0, 2.5)}


def _hex(af, h, z0=0.0):
    R = af / math.sqrt(3)
    pts = [V(R * math.cos(math.radians(60 * i)), R * math.sin(math.radians(60 * i)), z0) for i in range(6)]
    return Part.Face(Part.makePolygon(pts + [pts[0]])).extrude(V(0, 0, h))


@op("add_fastener")
def add_fastener(doc, name, kind, size, length=None, position=(0, 0, 0), direction=(0, 0, -1)):
    if size not in FAST:
        raise ValueError(f"size must be one of {sorted(FAST, key=lambda s: int(s[1:]))}")
    dk, k, sk, nut_af, nut_m, w1, w2, wh = FAST[size]
    d = float(size[1:])
    if kind in ("shcs", "hex_bolt"):
        if not length:
            raise ValueError("length (mm, under head) is required for bolts")
        L = float(length)
        shank = Part.makeCylinder(d / 2, L)                       # shank along +Z from the bearing face
        if kind == "shcs":
            head = Part.makeCylinder(dk / 2, k, V(0, 0, -k))
            s = head.fuse(shank)
            s = s.cut(_hex(sk, 0.6 * k + 0.1, -k - 0.1 + 0.4 * k))   # hex socket from the head top
            pn, desc = f"ISO 4762 {size}x{int(L) if L == int(L) else L}", f"Socket head cap screw {size}x{L:g}"
        else:
            head = _hex(nut_af, k * 0.7, -k * 0.7)
            s = head.fuse(shank)
            pn, desc = f"ISO 4017 {size}x{L:g}", f"Hex bolt {size}x{L:g}"
    elif kind == "hex_nut":
        s = _hex(nut_af, nut_m).cut(Part.makeCylinder(d / 2, nut_m + 2, V(0, 0, -1)))
        pn, desc = f"ISO 4032 {size}", f"Hex nut {size}"
    elif kind == "washer":
        s = Part.makeCylinder(w2 / 2, wh).cut(Part.makeCylinder(w1 / 2, wh + 2, V(0, 0, -1)))
        pn, desc = f"ISO 7089 {size}", f"Plain washer {size}"
    else:
        raise ValueError("kind must be shcs|hex_bolt|hex_nut|washer")
    s = s.removeSplitter()
    dv = vec(direction); dv.normalize()
    rot = _rot_between(V(0, 0, 1), dv)
    s.Placement = App.Placement(vec(position), rot)
    o = add_shape(doc, name, s, "steel")
    o.PartNo, o.Description, o.Group = pn, desc, "fasteners"
    return describe(o)


# ----------------------------------------------------------------------------- clearance
@op("check_clearance", mutates=False)
def check_clearance(doc, objects=None, report_below_mm=5.0):
    objs = pick(doc, objects)
    pairs = []
    for a in range(len(objs)):
        for b in range(a + 1, len(objs)):
            dist, pts, _ = objs[a].Shape.distToShape(objs[b].Shape)
            if dist <= report_below_mm:
                pairs.append(dict(a=objs[a].Name, b=objs[b].Name, min_distance_mm=round(dist, 4),
                                  point_on_a=r3(pts[0][0]), point_on_b=r3(pts[0][1])))
    pairs.sort(key=lambda p: p["min_distance_mm"])
    return dict(objects=len(objs), report_below_mm=report_below_mm, pairs=pairs,
                smallest_mm=pairs[0]["min_distance_mm"] if pairs else None)


# ----------------------------------------------------------------------------- section render export
AX = {"x": V(1, 0, 0), "y": V(0, 1, 0), "z": V(0, 0, 1)}


@op("export_section_for_render", mutates=False)
def export_section_for_render(doc, out_dir, axis, offset, remove="above", objects=None, deflection=0.05):
    import MeshPart
    n = AX[axis]
    os.makedirs(out_dir, exist_ok=True)
    big = 1e5
    # half-space box occupying the part to REMOVE
    base = {"x": V(0, -big / 2, -big / 2), "y": V(-big / 2, 0, -big / 2), "z": V(-big / 2, -big / 2, 0)}[axis]
    dims = {"x": (big, big, big), "y": (big, big, big), "z": (big, big, big)}[axis]
    box = Part.makeBox(*dims, base)
    if remove == "above":
        box.translate(n * float(offset))
    else:
        box.translate(n * (float(offset) - big))
    items = []
    for o in pick(doc, objects):
        s = o.Shape.cut(box)
        if s.isNull() or not s.Solids:
            continue
        m = MeshPart.meshFromShape(Shape=s, LinearDeflection=float(deflection), AngularDeflection=0.25)
        p = os.path.join(out_dir, o.Name + "_sec.stl"); m.write(p)
        mt = meta(o)
        items.append(dict(name=o.Name, stl=p, material=mt["material"], color=mt["color"]))
    return dict(items=items)


# ----------------------------------------------------------------------------- DXF (own minimal R12 writer, no GUI needed)
def _dxf(entities, path):
    L = ["0", "SECTION", "2", "ENTITIES"]
    for e in entities:
        if e[0] == "LINE":
            L += ["0", "LINE", "8", "0", "10", f"{e[1][0]:.5f}", "20", f"{e[1][1]:.5f}", "11", f"{e[2][0]:.5f}", "21", f"{e[2][1]:.5f}"]
        elif e[0] == "CIRCLE":
            L += ["0", "CIRCLE", "8", "0", "10", f"{e[1][0]:.5f}", "20", f"{e[1][1]:.5f}", "40", f"{e[2]:.5f}"]
    L += ["0", "ENDSEC", "0", "EOF"]
    open(path, "w").write("\n".join(L) + "\n")


def _basis_for(n):
    n = V(n); n.normalize()
    ref = V(1, 0, 0) if abs(n.x) < 0.9 else V(0, 1, 0)
    u = ref.cross(n); u.normalize(); v = n.cross(u)
    return u, v


@op("export_dxf", mutates=False)
def export_dxf(doc, path, name, face=None, plane=None, offset=0.0):
    """2D outline for laser/waterjet: a planar face of an object, or a section of the object by an axis-aligned plane."""
    o = get(doc, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if face is not None:
        i = _one(o.Shape, face)
        f, n, c = _face_n(o.Shape, i)
        if f.Surface.TypeId != "Part::GeomPlane":
            raise ValueError("face must be planar")
        edges = f.Edges
        origin = c
    elif plane:
        n = AX[plane.lower()]
        wires = o.Shape.slice(n, float(offset))
        if not wires:
            raise ValueError("plane does not intersect the object")
        edges = [e for w in wires for e in w.Edges]
        origin = n * float(offset)
    else:
        raise ValueError("give face or plane")
    u, v = _basis_for(n)
    ents = []
    for e in edges:
        t = e.Curve.TypeId
        if t == "Part::GeomCircle" and e.isClosed():
            p = e.Curve.Center - origin
            ents.append(("CIRCLE", (p.dot(u), p.dot(v)), e.Curve.Radius))
        elif t == "Part::GeomLine":
            a, b = e.Vertexes[0].Point - origin, e.Vertexes[-1].Point - origin
            ents.append(("LINE", (a.dot(u), a.dot(v)), (b.dot(u), b.dot(v))))
        else:
            pts = e.discretize(Deflection=0.02)
            for p0, p1 in zip(pts[:-1], pts[1:]):
                a, b = p0 - origin, p1 - origin
                ents.append(("LINE", (a.dot(u), a.dot(v)), (b.dot(u), b.dot(v))))
    # shift so the lower-left corner of the outline is the DXF origin (easier to nest/cut)
    xs = [q[0] for e in ents for q in ([e[1], e[2]] if e[0] == "LINE" else [(e[1][0] - e[2], 0)])]
    ys = [q[1] for e in ents for q in ([e[1], e[2]] if e[0] == "LINE" else [(0, e[1][1] - e[2])])]
    x0, y0 = min(xs), min(ys)
    ents = [("LINE", (e[1][0] - x0, e[1][1] - y0), (e[2][0] - x0, e[2][1] - y0)) if e[0] == "LINE" else ("CIRCLE", (e[1][0] - x0, e[1][1] - y0), e[2]) for e in ents]
    _dxf(ents, path)
    return dict(path=path, size_mm=[round(max(xs) - x0, 3), round(max(ys) - y0, 3)], entities=len(ents), circles=sum(1 for e in ents if e[0] == "CIRCLE"), bytes=os.path.getsize(path))
