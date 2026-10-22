"""Sketch-based modelling and feature operations."""
import math
import FreeCAD as App
import Part
import Sketcher
from FreeCAD import Vector as V

from cad_mcp.core import op, get, add_shape, describe, vec, resolve_faces, com

# sketch plane -> placement. sketch x,y -> world axes; normal = pad direction
PLANES = {
    "XY": (App.Rotation(), "+Z"),
    "XZ": (App.Rotation(V(1, 0, 0), 90), "-Y"),             # sketch (x,y) -> world (x,z); normal is -Y (FreeCAD convention)
    "YZ": (App.Rotation(V(1, 1, 1), 120), "+X"),            # sketch (x,y) -> world (y,z); normal +X
}


def _pt(p):
    return V(float(p[0]), float(p[1]), 0)


@op("create_sketch")
def create_sketch(doc, name, plane="XY", offset=0.0, geometry=None, constraints=None):
    if plane not in PLANES:
        raise ValueError("plane must be XY, XZ or YZ")
    if doc.getObject(name):
        raise ValueError(f"object {name!r} already exists")
    sk = doc.addObject("Sketcher::SketchObject", name)
    rot, normal = PLANES[plane]
    nvec = rot.multVec(V(0, 0, 1))
    sk.Placement = App.Placement(nvec * float(offset), rot)
    for g in geometry or []:
        t = g["type"]
        if t == "line":
            sk.addGeometry(Part.LineSegment(_pt(g["p1"]), _pt(g["p2"])), bool(g.get("construction", False)))
        elif t == "circle":
            sk.addGeometry(Part.Circle(_pt(g["center"]), V(0, 0, 1), float(g["radius"])), bool(g.get("construction", False)))
        elif t == "arc":       # counter-clockwise from start_deg to end_deg
            c = Part.Circle(_pt(g["center"]), V(0, 0, 1), float(g["radius"]))
            sk.addGeometry(Part.ArcOfCircle(c, math.radians(g["start_deg"]), math.radians(g["end_deg"])), bool(g.get("construction", False)))
        elif t == "polyline":   # convenience: closed polygon from points
            pts = g["points"]
            for i in range(len(pts)):
                sk.addGeometry(Part.LineSegment(_pt(pts[i]), _pt(pts[(i + 1) % len(pts)])), False)
        else:
            raise ValueError(f"unknown geometry type {t!r}")
    n = len(sk.Geometry)
    if g_poly := [g for g in (geometry or []) if g["type"] == "polyline"]:
        pass
    C = Sketcher.Constraint
    for c in constraints or []:
        t = c["type"]
        a, b = c.get("geo"), c.get("geo2")
        if t == "coincident": sk.addConstraint(C("Coincident", a, c["pos"], b, c["pos2"]))
        elif t == "horizontal": sk.addConstraint(C("Horizontal", a))
        elif t == "vertical": sk.addConstraint(C("Vertical", a))
        elif t == "parallel": sk.addConstraint(C("Parallel", a, b))
        elif t == "perpendicular": sk.addConstraint(C("Perpendicular", a, b))
        elif t == "equal": sk.addConstraint(C("Equal", a, b))
        elif t == "tangent": sk.addConstraint(C("Tangent", a, b))
        elif t == "radius": sk.addConstraint(C("Radius", a, float(c["value"])))
        elif t == "diameter": sk.addConstraint(C("Diameter", a, float(c["value"])))
        elif t == "length": sk.addConstraint(C("Distance", a, float(c["value"])))
        elif t == "distance_x": sk.addConstraint(C("DistanceX", a, c.get("pos", 1), b, c.get("pos2", 1), float(c["value"])) if b is not None else C("DistanceX", a, 1, a, 2, float(c["value"])))
        elif t == "distance_y": sk.addConstraint(C("DistanceY", a, c.get("pos", 1), b, c.get("pos2", 1), float(c["value"])) if b is not None else C("DistanceY", a, 1, a, 2, float(c["value"])))
        elif t == "lock": sk.addConstraint(C("DistanceX", -1, 1, a, c.get("pos", 1), float(c["x"]))); sk.addConstraint(C("DistanceY", -1, 1, a, c.get("pos", 1), float(c["y"])))
        else:
            raise ValueError(f"unknown constraint {t!r}")
    doc.recompute()
    info = dict(name=name, plane=plane, normal=normal, geometry=len(sk.Geometry), constraints=len(sk.Constraints))
    try:
        info["solver_dof"] = sk.solve()          # 0 = fully constrained, >0 degrees of freedom left, <0 failure
    except Exception:
        pass
    wires = _wires(sk)
    info["closed_wires"] = sum(1 for w in wires if w.isClosed())
    info["open_wires"] = sum(1 for w in wires if not w.isClosed())
    return info


def _wires(sk):
    return [Part.Wire(es) for es in Part.sortEdges(sk.Shape.Edges)] if sk.Shape.Edges else []


def _face(sk):
    ws = [w for w in _wires(sk) if w.isClosed()]
    if not ws:
        raise ValueError(f"sketch {sk.Name!r} has no closed wire (check that geometry end points coincide)")
    return Part.makeFace(ws, "Part::FaceMakerBullseye")   # nested closed wires become holes


def _normal(sk):
    return sk.Placement.Rotation.multVec(V(0, 0, 1))


def _keep_or_delete(doc, names, keep):
    if not keep:
        for n in names:
            doc.removeObject(n)


def _inherit(o):
    return (o.Material or None)


@op("pad")
def pad(doc, sketch, length, result_name, symmetric=False, reverse=False, material=None, color=None, keep_sketch=True):
    sk = get(doc, sketch)
    f = _face(sk)
    n = _normal(sk) * (-1 if reverse else 1)
    if symmetric:
        f = f.copy(); f.translate(n * (-float(length) / 2))
    s = f.extrude(n * float(length))
    o = add_shape(doc, result_name, s, material, color)
    return describe(o)


@op("pocket")
def pocket(doc, sketch, target, depth=None, reverse=False, through_all=False, result_name=None):
    sk = get(doc, sketch); tgt = get(doc, target)
    f = _face(sk)
    n = _normal(sk) * (1 if reverse else -1)          # pockets cut opposite to the sketch normal by default
    d = 10000.0 if through_all or depth is None else float(depth)
    tool = f.extrude(n * d)
    back = f.copy(); back.translate(n * -0.01); tool = back.extrude(n * (d + 0.01))
    s = tgt.Shape.cut(tool)
    if result_name:
        return describe(add_shape(doc, result_name, s, _inherit(tgt)))
    tgt.Shape = s if s.ShapeType != "Compound" or len(s.Solids) != 1 else s.Solids[0]
    return describe(tgt)


@op("revolve")
def revolve(doc, sketch, result_name, axis_point=(0, 0, 0), axis_dir=(0, 1, 0), angle_deg=360.0, material=None):
    sk = get(doc, sketch)
    s = _face(sk).revolve(vec(axis_point), vec(axis_dir), float(angle_deg))
    return describe(add_shape(doc, result_name, s, material))


@op("loft")
def loft(doc, sketches, result_name, ruled=False, material=None):
    ws = []
    for n in sketches:
        w = [w for w in _wires(get(doc, n)) if w.isClosed()]
        if not w:
            raise ValueError(f"sketch {n!r} has no closed wire")
        ws.append(w[0])
    s = Part.makeLoft(ws, True, bool(ruled))
    return describe(add_shape(doc, result_name, s, material))


@op("sweep")
def sweep(doc, profile_sketch, result_name, path_sketch=None, path_points=None, frenet=True, material=None):
    prof = [w for w in _wires(get(doc, profile_sketch)) if w.isClosed()][0]
    if path_sketch:
        path = Part.Wire(Part.sortEdges(get(doc, path_sketch).Shape.Edges)[0])
    elif path_points:
        path = Part.makePolygon([vec(p) for p in path_points])
    else:
        raise ValueError("give path_sketch or path_points")
    s = path.makePipeShell([prof], True, bool(frenet))
    return describe(add_shape(doc, result_name, s, material))


@op("hole")
def hole(doc, name, position, direction, diameter, depth=None, kind="simple", cbore_diameter=None, cbore_depth=None, csink_diameter=None):
    o = get(doc, name)
    pos, d = vec(position), vec(direction); d.normalize()
    L = 10000.0 if depth is None else float(depth)
    start = pos - d * 0.5                     # begin slightly outside so the cut is never coplanar
    parts = [Part.makeCylinder(diameter / 2.0, L + 0.5, start, d)]
    if kind == "counterbore":
        parts.append(Part.makeCylinder(cbore_diameter / 2.0, float(cbore_depth) + 0.5, start, d))
    elif kind == "countersink":
        r2 = csink_diameter / 2.0; r1 = diameter / 2.0
        h = (r2 - r1)                         # 90 degree countersink
        parts.append(Part.makeCone(r2 + 0.5, r1, h + 0.5, start, d))
    s = o.Shape.cut(parts)
    if not s.Volume < o.Shape.Volume - 1e-6:
        raise ValueError("hole removed no material: is the position on/inside the part and the direction pointing into it?")
    o.Shape = s.Solids[0] if len(s.Solids) == 1 else s
    return describe(o)


@op("shell")
def shell(doc, name, thickness, remove_faces):
    o = get(doc, name)
    ids = resolve_faces(o.Shape, remove_faces)
    s = o.Shape.makeThickness([o.Shape.Faces[i - 1] for i in ids], -abs(float(thickness)), 1e-3)
    if not s.isValid():
        raise ValueError("shell produced an invalid solid; try a smaller thickness or different faces")
    o.Shape = s
    return describe(o)


@op("mirror")
def mirror(doc, name, plane, result_name, plane_offset=0.0, fuse=False):
    o = get(doc, name)
    nrm = {"XY": V(0, 0, 1), "XZ": V(0, 1, 0), "YZ": V(1, 0, 0)}[plane]
    s = o.Shape.mirror(nrm * float(plane_offset), nrm)
    if fuse:
        s = o.Shape.fuse(s).removeSplitter()
    return describe(add_shape(doc, result_name, s, _inherit(o)))


def _pattern(doc, name, result_name, copies, fuse):
    o = get(doc, name)
    if fuse:
        s = o.Shape
        for c in copies:
            s = s.fuse(c)
        return describe(add_shape(doc, result_name, s.removeSplitter(), _inherit(o)))
    out = []
    for i, c in enumerate(copies, start=1):
        out.append(add_shape(doc, f"{result_name}_{i}", c, _inherit(o)).Name)
    return dict(created=out)


@op("linear_pattern")
def linear_pattern(doc, name, direction, count, spacing, result_name, fuse=False):
    o = get(doc, name); d = vec(direction); d.normalize()
    copies = []
    for i in range(1, int(count)):
        c = o.Shape.copy(); c.translate(d * (float(spacing) * i)); copies.append(c)
    return _pattern(doc, name, result_name, copies, fuse)


@op("polar_pattern")
def polar_pattern(doc, name, count, result_name, axis=(0, 0, 1), center=(0, 0, 0), total_angle_deg=360.0, fuse=False):
    o = get(doc, name)
    n = int(count)
    step = float(total_angle_deg) / (n if abs(total_angle_deg - 360.0) < 1e-9 else max(n - 1, 1))
    copies = []
    for i in range(1, n):
        c = o.Shape.copy(); c.rotate(vec(center), vec(axis), step * i); copies.append(c)
    return _pattern(doc, name, result_name, copies, fuse)
