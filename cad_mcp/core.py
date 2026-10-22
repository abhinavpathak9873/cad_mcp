"""Worker runtime: loads the document, runs ONE registered operation, saves, writes a JSON result file.

The server writes a tiny job script (see server.py) that calls `core.main()`. The job description comes via the
FCMCP_JOB env var (path to a JSON file): {op, doc, args, result}. Operations are plain functions
`fn(doc, **args) -> dict` registered with @op(name, mutates=...).
"""
import json, os, re, sys, traceback

import FreeCAD as App
import Part
from FreeCAD import Vector as V

from cad_mcp.materials import get_material

OPS = {}

def op(name, mutates=True, needs_doc=True):
    """Register a worker operation. mutates=False -> the document is not saved afterwards."""
    def deco(fn):
        OPS[name] = dict(fn=fn, mutates=mutates, needs_doc=needs_doc)
        return fn
    return deco

# ----------------------------------------------------------------------------- helpers
def vec(v):
    return V(float(v[0]), float(v[1]), float(v[2]))

def r3(v, n=4):
    return [round(float(v.x), n), round(float(v.y), n), round(float(v.z), n)]

def solids_objects(doc):
    return [o for o in doc.Objects if o.isDerivedFrom("Part::Feature") and hasattr(o, "Shape")
            and not o.Shape.isNull() and o.Shape.Solids and not o.Name.startswith("_")]

def get(doc, name):
    o = doc.getObject(name)
    if o is None:
        names = [x.Name for x in doc.Objects if not x.Name.startswith("_")]
        raise ValueError(f"no object named {name!r}; objects: {names}")
    return o

def pick(doc, names=None):
    """names None/[]/'all' -> every solid object; else the named objects."""
    if not names or names == "all" or names == ["all"]:
        return solids_objects(doc)
    if isinstance(names, str):
        names = [names]
    return [get(doc, n) for n in names]

META = [("Material", "App::PropertyString"), ("PartNo", "App::PropertyString"),
        ("Description", "App::PropertyString"), ("Supplier", "App::PropertyString"),
        ("Color", "App::PropertyString"), ("Quantity", "App::PropertyInteger"),
        ("Group", "App::PropertyString"), ("Source", "App::PropertyString")]

def ensure_meta(o):
    for p, t in META:
        if p not in o.PropertiesList:
            o.addProperty(t, p, "Meta")
            if p == "Quantity":
                o.Quantity = 1

def meta(o):
    ensure_meta(o)
    return dict(material=o.Material, part_no=o.PartNo, description=o.Description, supplier=o.Supplier,
                color=o.Color, quantity=o.Quantity, group=o.Group)

def clean_name(n):
    n = re.sub(r"\W", "_", str(n))
    return ("_" + n) if n and n[0].isdigit() else n

def com(s):
    """Centre of mass for a solid or a compound of solids."""
    if hasattr(s, "CenterOfMass"):
        return s.CenterOfMass
    tot, acc = 0.0, V(0, 0, 0)
    for so in s.Solids:
        acc = acc + so.CenterOfMass * so.Volume
        tot += so.Volume
    return acc * (1.0 / tot)

def add_shape(doc, name, shape, material=None, color=None, source=""):
    if shape.ShapeType in ("Compound", "CompSolid") and len(shape.Solids) == 1 and not shape.Faces[len(shape.Solids[0].Faces):]:
        shape = shape.Solids[0]
    name = clean_name(name)
    if doc.getObject(name) is not None:
        raise ValueError(f"object {name!r} already exists (delete it or choose another name)")
    o = doc.addObject("Part::Feature", name)
    o.Shape = shape
    ensure_meta(o)
    if material:
        o.Material = get_material(material)[0]
    if color:
        o.Color = ",".join(str(float(c)) for c in color)
    o.Source = source
    return o

def placement(position=None, rotation_axis=None, rotation_deg=0.0):
    return App.Placement(vec(position or (0, 0, 0)), App.Rotation(vec(rotation_axis or (0, 0, 1)), float(rotation_deg)))

def bbox(s):
    b = s.BoundBox
    return dict(min=[round(b.XMin, 4), round(b.YMin, 4), round(b.ZMin, 4)], max=[round(b.XMax, 4), round(b.YMax, 4), round(b.ZMax, 4)],
                size=[round(b.XLength, 4), round(b.YLength, 4), round(b.ZLength, 4)])

def describe(o):
    s = o.Shape
    d = dict(name=o.Name, type=s.ShapeType, valid=bool(s.isValid()), solids=len(s.Solids), faces=len(s.Faces), edges=len(s.Edges),
             bbox=bbox(s), meta=meta(o))
    if s.Solids:
        d.update(volume_mm3=round(s.Volume, 4), area_mm2=round(s.Area, 4), center_of_mass=r3(com(s)))
    return d

# ----------------------------------------------------------------------------- selectors
AXES = {"X": V(1, 0, 0), "Y": V(0, 1, 0), "Z": V(0, 0, 1)}

def _axis_token(t):
    sign = -1 if t.startswith("-") else 1
    return AXES[t.lstrip("+-").upper()] * sign

def _coord(v, ax):
    return {"x": v.x, "y": v.y, "z": v.z}[ax]

def _select(items, centers, spec, kind):
    """Shared selector engine. `items` are Faces or Edges, `centers` their centre points."""
    idx = list(range(len(items)))
    for tok in spec.split():
        tok = tok.strip()
        if tok == "all":
            continue
        if tok.startswith("index:"):
            keep = {int(i) for i in tok[6:].split(",")}
            idx = [i for i in idx if (i + 1) in keep]
        elif tok.startswith("normal:") and kind == "face":
            ax = _axis_token(tok[7:])
            idx = [i for i in idx if items[i].Surface.TypeId == "Part::GeomPlane"
                   and items[i].normalAt(*items[i].Surface.parameter(items[i].CenterOfMass)).getAngle(ax) < 0.02]
        elif tok.startswith("parallel:") and kind == "edge":
            ax = _axis_token(tok[9:])
            idx = [i for i in idx if items[i].Curve.TypeId == "Part::GeomLine"
                   and abs(abs(items[i].tangentAt(items[i].FirstParameter).dot(ax)) - 1) < 1e-6]
        elif re.match(r"^[xyz]:", tok):
            ax, val = tok[0], tok[2:]
            vals = [_coord(centers[i], ax) for i in idx]
            if not vals:
                continue
            target = max(vals) if val == "max" else min(vals) if val == "min" else float(val)
            idx = [i for i in idx if abs(_coord(centers[i], ax) - target) < 1e-3]
        elif tok.startswith("type:"):
            want = tok[5:].lower()
            def ok(i):
                t = (items[i].Surface.TypeId if kind == "face" else items[i].Curve.TypeId).lower()
                return want in t
            idx = [i for i in idx if ok(i)]
        elif tok.startswith("radius:"):
            r = float(tok[7:])
            def okr(i):
                try:
                    return abs((items[i].Surface if kind == "face" else items[i].Curve).Radius - r) < 1e-3
                except Exception:
                    return False
            idx = [i for i in idx if okr(i)]
        elif re.match(r"^(area|length)[<>]", tok):
            key = "Area" if tok.startswith("area") else "Length"
            op_, val = tok[len(key.lower())], float(tok[len(key.lower()) + 1:])
            idx = [i for i in idx if (getattr(items[i], key) > val if op_ == ">" else getattr(items[i], key) < val)]
        else:
            raise ValueError(f"unknown selector token {tok!r}")
    return [i + 1 for i in idx]

def resolve_faces(shape, spec):
    """Accepts one spec or a list of specs (strings / index lists) which are united, e.g. ['normal:-Z z:min', [4, 7]]."""
    if isinstance(spec, (list, tuple)) and spec and any(isinstance(x, (str, list, tuple)) for x in spec):
        out = []
        for sp in spec:
            for i in resolve_faces(shape, sp):
                if i not in out:
                    out.append(i)
        return out
    if isinstance(spec, int):
        spec = [spec]
    return _resolve_faces(shape, spec)

def _resolve_faces(shape, spec):
    """spec: list of 1-based face indices, or a selector string (see list_faces docstring). Returns 1-based indices."""
    if isinstance(spec, (list, tuple)):
        for i in spec:
            if not 1 <= int(i) <= len(shape.Faces):
                raise ValueError(f"face index {i} out of range 1..{len(shape.Faces)}")
        return [int(i) for i in spec]
    centers = [f.CenterOfMass for f in shape.Faces]
    return _select(shape.Faces, centers, str(spec), "face")

def resolve_edges(shape, spec):
    if isinstance(spec, int):
        spec = [spec]
    if isinstance(spec, (list, tuple)):
        for i in spec:
            if not 1 <= int(i) <= len(shape.Edges):
                raise ValueError(f"edge index {i} out of range 1..{len(shape.Edges)}")
        return [int(i) for i in spec]
    centers = [e.CenterOfMass if hasattr(e, "CenterOfMass") else e.valueAt((e.FirstParameter + e.LastParameter) / 2) for e in shape.Edges]
    return _select(shape.Edges, centers, str(spec), "edge")

# ----------------------------------------------------------------------------- entry point
def main():
    job = json.load(open(os.environ["FCMCP_JOB"]))
    res = dict(ok=True)
    try:
        spec = OPS[job["op"]]
        if spec["needs_doc"]:
            path = job["doc"]
            if job["op"] == "new_document":
                doc = App.newDocument(job["args"].get("name", "Doc"))
            else:
                if not os.path.exists(path):
                    raise ValueError(f"document not found: {path} (create it with new_document)")
                doc = App.openDocument(path)
        else:
            doc = None
        out = spec["fn"](doc, **job["args"])
        if doc is not None and spec["mutates"]:
            doc.recompute()
            doc.saveAs(job["doc"])
        res["result"] = out
    except Exception as ex:
        res = dict(ok=False, error=f"{type(ex).__name__}: {ex}", trace=traceback.format_exc()[-1800:])
    json.dump(res, open(job["result"], "w"), default=str)
    sys.stdout.flush()

# operation modules register themselves on import
from cad_mcp import ops_model, ops_analysis, ops_fem, ops_features, ops_docs, ops_assembly  # noqa: E402,F401
