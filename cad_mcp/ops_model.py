"""Modelling, inspection and export operations (run inside freecadcmd)."""
import os
import FreeCAD as App
import Part
from FreeCAD import Vector as V

from cad_mcp.core import (op, vec, r3, get, pick, add_shape, placement, describe, meta, ensure_meta, bbox,
                                 resolve_faces, resolve_edges, solids_objects, clean_name)
from cad_mcp.materials import get_material, MATERIALS


@op("new_document", mutates=True)
def new_document(doc, name="Doc"):
    return dict(created=name)


@op("document_info", mutates=False)
def document_info(doc):
    objs = [o for o in doc.Objects if not o.Name.startswith("_")]
    return dict(label=doc.Label, objects=[o.Name for o in objs], count=len(objs))


@op("list_objects", mutates=False)
def list_objects(doc):
    return dict(objects=[describe(o) for o in doc.Objects if o.isDerivedFrom("Part::Feature") and not o.Name.startswith("_")])


@op("add_primitive")
def add_primitive(doc, name, shape, dimensions, position=None, rotation_axis=None, rotation_deg=0.0, material=None, color=None):
    d = {k: float(v) for k, v in dimensions.items()}
    if shape == "box":
        s = Part.makeBox(d["length"], d["width"], d["height"])
    elif shape == "cylinder":
        s = Part.makeCylinder(d["radius"], d["height"])
    elif shape == "sphere":
        s = Part.makeSphere(d["radius"])
    elif shape == "cone":
        s = Part.makeCone(d["radius1"], d["radius2"], d["height"])
    elif shape == "torus":
        s = Part.makeTorus(d["radius1"], d["radius2"])
    else:
        raise ValueError("shape must be box|cylinder|sphere|cone|torus")
    s.Placement = placement(position, rotation_axis, rotation_deg)
    o = add_shape(doc, name, s, material, color)
    return describe(o)


def _plane_pts(plane, pts):
    plane = plane.upper()
    if plane == "XY":
        return [V(a, b, 0) for a, b in pts], V(0, 0, 1)
    if plane == "XZ":
        return [V(a, 0, b) for a, b in pts], V(0, 1, 0)
    if plane == "YZ":
        return [V(0, a, b) for a, b in pts], V(1, 0, 0)
    raise ValueError("plane must be XY, XZ or YZ")


@op("extrude_polygon")
def extrude_polygon(doc, name, points, height, plane="XY", offset=0.0, holes=None, material=None, color=None):
    def face(poly):
        p3, n = _plane_pts(plane, poly)
        p3 = [p + n * offset for p in p3]
        return Part.Face(Part.makePolygon(p3 + [p3[0]])), n
    f, n = face(points)
    for h in holes or []:
        hf, _ = face(h)
        f = f.cut(hf)
    s = f.extrude(n * float(height))
    return describe(add_shape(doc, name, s, material, color))


@op("boolean")
def boolean(doc, operation, base, tools, result_name, keep_inputs=False):
    b = get(doc, base)
    ts = [get(doc, t) for t in tools]
    shape = b.Shape.copy()
    if operation == "cut":
        shape = shape.cut([t.Shape for t in ts])
    elif operation == "fuse":
        shape = shape.fuse([t.Shape for t in ts])
        shape = shape.removeSplitter()
    elif operation == "common":
        for t in ts:
            shape = shape.common(t.Shape)
    else:
        raise ValueError("operation must be cut|fuse|common")
    mat, col = (b.Material or None), (b.Color or None)
    if not keep_inputs:
        for o in [b] + ts:
            doc.removeObject(o.Name)
    o = add_shape(doc, result_name, shape, mat, [float(c) for c in col.split(",")] if col else None)
    return describe(o)


@op("transform")
def transform(doc, name, translate=None, rotation_axis=None, rotation_deg=0.0, rotation_center=None, copy_as=None):
    o = get(doc, name)
    s = o.Shape.copy()
    if rotation_deg:
        s.rotate(vec(rotation_center or (0, 0, 0)), vec(rotation_axis or (0, 0, 1)), float(rotation_deg))
    if translate:
        s.translate(vec(translate))
    if copy_as:
        n = add_shape(doc, copy_as, s, o.Material or None)
        n.PartNo, n.Description = o.PartNo, o.Description
        return describe(n)
    o.Shape = s
    return describe(o)


def _dress(doc, name, radius, edges, result_name, kind):
    o = get(doc, name)
    s = o.Shape
    ids = resolve_edges(s, edges if edges is not None else "all")
    if not ids:
        raise ValueError("edge selector matched no edges")
    es = [s.Edges[i - 1] for i in ids]
    try:
        ns = s.makeFillet(float(radius), es) if kind == "fillet" else s.makeChamfer(float(radius), es)
    except Exception as ex:
        raise ValueError(f"{kind} failed ({ex}); try a smaller size or fewer edges")
    if not ns.isValid():
        raise ValueError(f"{kind} produced an invalid solid; try a smaller size")
    if result_name:
        n = add_shape(doc, result_name, ns, o.Material or None)
        return dict(describe(n), edges_used=ids)
    o.Shape = ns
    return dict(describe(o), edges_used=ids)


@op("fillet")
def fillet(doc, name, radius, edges=None, result_name=None):
    return _dress(doc, name, radius, edges, result_name, "fillet")


@op("chamfer")
def chamfer(doc, name, size, edges=None, result_name=None):
    return _dress(doc, name, size, edges, result_name, "chamfer")


@op("delete_object")
def delete_object(doc, name):
    get(doc, name)
    doc.removeObject(name)
    return dict(deleted=name)


@op("set_properties")
def set_properties(doc, name, material=None, part_no=None, description=None, supplier=None, color=None, quantity=None, group=None, new_name=None):
    o = get(doc, name)
    ensure_meta(o)
    if material is not None:
        o.Material = get_material(material)[0]
        if not o.Color and not color:
            o.Color = ",".join(str(c) for c in MATERIALS[o.Material]["color"])
    if part_no is not None: o.PartNo = part_no
    if description is not None: o.Description = description
    if supplier is not None: o.Supplier = supplier
    if color is not None: o.Color = ",".join(str(float(c)) for c in color)
    if quantity is not None: o.Quantity = int(quantity)
    if group is not None: o.Group = group
    if new_name:
        o.Label = new_name
    return describe(o)


def _face_info(i, f):
    d = dict(index=i, type=f.Surface.TypeId.replace("Part::Geom", "").lower(), area=round(f.Area, 3), center=r3(f.CenterOfMass))
    if d["type"] == "plane":
        d["normal"] = r3(f.normalAt(*f.Surface.parameter(f.CenterOfMass)), 3)
    elif d["type"] in ("cylinder", "cone", "sphere", "torus"):
        d["radius"] = round(getattr(f.Surface, "Radius", 0.0), 4)
        if d["type"] == "cylinder":
            d["axis"] = r3(f.Surface.Axis, 3)
    return d


@op("list_faces", mutates=False)
def list_faces(doc, name, selector="all"):
    s = get(doc, name).Shape
    return dict(count=len(s.Faces), faces=[_face_info(i, s.Faces[i - 1]) for i in resolve_faces(s, selector)])


@op("list_edges", mutates=False)
def list_edges(doc, name, selector="all"):
    s = get(doc, name).Shape
    out = []
    for i in resolve_edges(s, selector):
        e = s.Edges[i - 1]
        t = e.Curve.TypeId.replace("Part::Geom", "").lower()
        mid = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
        d = dict(index=i, type=t, length=round(e.Length, 3), center=r3(mid))
        if t == "line":
            d["direction"] = r3(e.tangentAt(e.FirstParameter), 3)
        if t == "circle":
            d["radius"] = round(e.Curve.Radius, 4)
        out.append(d)
    return dict(count=len(s.Edges), edges=out)


# ----------------------------------------------------------------------------- import / export
@op("import_step")
def import_step(doc, path, name, material=None):
    s = Part.read(path)
    return describe(add_shape(doc, name, s, material, source=os.path.basename(path)))


@op("export", mutates=False)
def export(doc, fmt, path, objects=None):
    objs = pick(doc, objects)
    if not objs:
        raise ValueError("nothing to export")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fmt = fmt.lower()
    if fmt in ("step", "stp"):
        Part.export(objs, path)
    elif fmt in ("stl", "obj", "3mf"):
        import Mesh
        Mesh.export(objs, path)
    elif fmt == "fcstd":
        doc.saveCopy(path)
    else:
        raise ValueError("fmt must be step|stl|obj|3mf|fcstd (DXF: use export_dxf)")
    return dict(path=path, bytes=os.path.getsize(path), objects=[o.Name for o in objs])


@op("export_for_render", mutates=False)
def export_for_render(doc, out_dir, objects=None, deflection=0.05):
    """Tessellate each object to an STL via FreeCAD's MeshPart (the server renders them with numpy/matplotlib)."""
    import MeshPart
    os.makedirs(out_dir, exist_ok=True)
    items = []
    for o in pick(doc, objects):
        m = MeshPart.meshFromShape(Shape=o.Shape, LinearDeflection=float(deflection), AngularDeflection=0.25)
        p = os.path.join(out_dir, o.Name + ".stl")
        m.write(p)
        mt = meta(o)
        items.append(dict(name=o.Name, stl=p, material=mt["material"], color=mt["color"]))
    return dict(items=items)


@op("run_script")
def run_script(doc, code):
    """Execute user code. Globals: App, Part, V, doc, result (dict). Whatever is printed is returned."""
    import io, contextlib
    env = dict(App=App, Part=Part, V=V, FreeCAD=App, doc=doc, result={}, add_shape=add_shape)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(code, "<run_script>", "exec"), env)
    return dict(stdout=buf.getvalue()[-6000:], result=env["result"])
