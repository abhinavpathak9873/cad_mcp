#!/usr/bin/env python3
"""FreeCAD Engineering MCP server.

HOW THIS FILE WORKS (read top to bottom if you are learning MCP)
================================================================
An MCP server is a program that an AI client (Claude Code, Claude Desktop, ...) starts as a child process and talks
to with JSON-RPC 2.0 messages over stdin/stdout. The server advertises three kinds of things:

  * TOOLS     functions the model can call (@mcp.tool). The Python signature becomes the JSON schema and the
              docstring becomes the description the model reads. So docstrings here are written for the model.
  * RESOURCES read-only data addressed by URI (@mcp.resource), e.g. "freecad://documents".
  * PROMPTS   reusable prompt templates the user can pick (@mcp.prompt).

The official Python SDK (FastMCP) does the JSON-RPC plumbing; this file only declares tools.

STATELESS DESIGN: every tool call writes a small job description, starts `freecadcmd` in a subprocess on the
document workspace/<doc>.FCStd, the worker (freecad_worker/) performs ONE operation, saves the document and writes a
JSON result file that we return. No long-lived FreeCAD process, so a crash or a hang never corrupts the session.
State lives in the .FCStd files; before each modifying call the previous version is copied to workspace/.history/
which gives `undo`. IMPORTANT: stdout is the protocol channel, so never print() to it; log to stderr.

Ideas borrowed (not code) from neka-nat/freecad-mcp, bonninr/freecad_mcp and tessalabs-space/freecad-mcp: tool
granularity, a parts-library tool, image content for views, an execute-code escape hatch and a design prompt.
"""
import json, os, shlex, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

import numpy as np
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.fastmcp.exceptions import ToolError

import render as R
from freecad_worker.materials import MATERIALS

ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("FREECAD_MCP_WORKSPACE", ROOT / "workspace")).resolve()
FREECADCMD = os.environ.get("FREECADCMD", "freecadcmd")
LAUNCHER = shlex.split(os.environ.get("FREECAD_MCP_LAUNCHER", ""))   # e.g. "systemd-run --user --scope -p MemoryMax=16G"
TIMEOUT = int(os.environ.get("FREECAD_MCP_TIMEOUT", "240"))
LIBRARY = Path(os.environ.get("FREECAD_MCP_LIBRARY", ROOT / "library")).resolve()
HISTORY = 20            # undo depth per document

mcp = FastMCP("freecad-engineering")

Vec3 = list[float]
Faces = Union[list[int], str]


# ============================================================================ plumbing
def _doc_path(doc: str) -> Path:
    doc = doc.removesuffix(".FCStd")
    if not doc.replace("_", "").replace("-", "").isalnum():
        raise ToolError("document names may only contain letters, digits, _ and -")
    return WORKSPACE / f"{doc}.FCStd"


def _snapshot(path: Path):
    """Copy the current file into workspace/.history/<doc>/ before it is modified (this is what `undo` restores)."""
    if not path.exists():
        return
    h = WORKSPACE / ".history" / path.stem
    h.mkdir(parents=True, exist_ok=True)
    n = len(list(h.glob("*.FCStd")))
    shutil.copy2(path, h / f"{n:04d}.FCStd")
    for old in sorted(h.glob("*.FCStd"))[:-HISTORY]:
        old.unlink()


def run_worker(op: str, doc: Optional[str] = None, **args):
    """Run one worker operation in a fresh freecadcmd process and return its `result` dict."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    path = _doc_path(doc) if doc else None
    from freecad_worker import ops_registry   # noqa: F401  (pure-python table of which ops mutate)
    if path is not None and op in ops_registry.MUTATING and op != "new_document":
        _snapshot(path)
    if op == "new_document" and path.exists():
        raise ToolError(f"document {doc!r} already exists")
    with tempfile.TemporaryDirectory(prefix="fcmcp_") as td:
        job = dict(op=op, doc=str(path) if path else None, args=args, result=os.path.join(td, "result.json"))
        jp = os.path.join(td, "job.json")
        json.dump(job, open(jp, "w"))
        script = os.path.join(td, "job.py")      # the tiny script freecadcmd executes
        open(script, "w").write(f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nfrom freecad_worker import core\ncore.main()\n")
        env = dict(os.environ, FCMCP_JOB=jp, QT_QPA_PLATFORM="offscreen")
        cmd = LAUNCHER + [FREECADCMD, script]
        try:
            p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ToolError(f"FreeCAD timed out after {TIMEOUT}s running {op}")
        if not os.path.exists(job["result"]):
            raise ToolError(f"FreeCAD produced no result for {op} (exit {p.returncode}): {(p.stderr or p.stdout)[-800:]}")
        res = json.load(open(job["result"]))
    if not res["ok"]:
        raise ToolError(res["error"])
    return res["result"]


def _j(d) -> str:
    return json.dumps(d, indent=1)


def _out(subdir: str, doc: str, name: str) -> Path:
    p = WORKSPACE / subdir / doc.removesuffix(".FCStd")
    p.mkdir(parents=True, exist_ok=True)
    return p / name


# ============================================================================ documents
@mcp.tool()
def new_document(name: str) -> str:
    """Create an empty FreeCAD document `name` (saved as workspace/<name>.FCStd). Every other tool takes this `doc` name.
    Units are millimetres, +Z is up, angles are degrees."""
    return _j(run_worker("new_document", name, name=name))


@mcp.tool()
def list_documents() -> str:
    """List all documents in the workspace with size and modification time."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    return _j([dict(name=p.stem, bytes=p.stat().st_size, modified=time.strftime("%F %T", time.localtime(p.stat().st_mtime)))
               for p in sorted(WORKSPACE.glob("*.FCStd"))])


@mcp.tool()
def list_objects(doc: str) -> str:
    """List every solid/shape object in `doc` with type, validity, volume (mm3), area, bounding box, centre of mass and
    metadata (material, part number, ...). Use this to inspect state after each modelling step."""
    return _j(run_worker("list_objects", doc))


# ============================================================================ modelling
@mcp.tool()
def add_primitive(doc: str, name: str, shape: Literal["box", "cylinder", "sphere", "cone", "torus"], dimensions: dict[str, float],
                  position: Optional[Vec3] = None, rotation_axis: Optional[Vec3] = None, rotation_deg: float = 0.0,
                  material: Optional[str] = None, color: Optional[Vec3] = None) -> str:
    """Add a primitive solid. `dimensions` keys per shape: box {length(x), width(y), height(z)}; cylinder {radius, height}
    (axis +Z); sphere {radius}; cone {radius1, radius2, height}; torus {radius1 (ring), radius2 (tube)}.
    The shape is created with its origin corner/base centre at `position` [x,y,z] mm (default origin) after rotating
    `rotation_deg` about `rotation_axis` (default Z). Box origin is its min corner; cylinder/cone origin is the centre of
    the base circle. `material`: aluminium_6061, steel, stainless_304, pa12, pla, pom. `color`: [r,g,b] 0..1."""
    return _j(run_worker("add_primitive", doc, name=name, shape=shape, dimensions=dimensions, position=position,
                         rotation_axis=rotation_axis, rotation_deg=rotation_deg, material=material, color=color))


@mcp.tool()
def extrude_polygon(doc: str, name: str, points: list[list[float]], height: float, plane: Literal["XY", "XZ", "YZ"] = "XY",
                    offset: float = 0.0, holes: Optional[list[list[list[float]]]] = None, material: Optional[str] = None,
                    color: Optional[Vec3] = None) -> str:
    """Extrude a closed 2D polygon (list of [a,b] points, no need to repeat the first) by `height` mm.
    plane XY: points are (x,y), extrusion along +Z. XZ: points are (x,z), extrusion along +Y. YZ: points are (y,z),
    extrusion along +X. `offset` moves the profile along the extrusion axis first. `holes` is an optional list of
    polygons cut out of the profile. Example L-bracket profile in XZ: [[0,0],[60,0],[60,5],[5,5],[5,40],[0,40]]."""
    return _j(run_worker("extrude_polygon", doc, name=name, points=points, height=height, plane=plane, offset=offset,
                         holes=holes, material=material, color=color))


@mcp.tool()
def boolean(doc: str, operation: Literal["cut", "fuse", "common"], base: str, tools: list[str], result_name: str,
            keep_inputs: bool = False) -> str:
    """Boolean combine objects. cut: base minus all `tools`; fuse: union of base and tools (coplanar faces merged);
    common: intersection. The inputs are deleted unless keep_inputs=true; the result object is `result_name` and keeps
    the base's material. Avoid exactly coplanar/tangent tool faces: extend cutting tools slightly past the surface."""
    return _j(run_worker("boolean", doc, operation=operation, base=base, tools=tools, result_name=result_name, keep_inputs=keep_inputs))


@mcp.tool()
def transform(doc: str, name: str, translate: Optional[Vec3] = None, rotation_axis: Optional[Vec3] = None, rotation_deg: float = 0.0,
              rotation_center: Optional[Vec3] = None, copy_as: Optional[str] = None) -> str:
    """Rotate then translate an object (mm, degrees). Rotation is about `rotation_axis` through `rotation_center`
    (default world Z through the origin), applied before the translation. With `copy_as` a new object is created and
    the original stays where it is (use for repeated parts)."""
    return _j(run_worker("transform", doc, name=name, translate=translate, rotation_axis=rotation_axis,
                         rotation_deg=rotation_deg, rotation_center=rotation_center, copy_as=copy_as))


@mcp.tool()
def fillet(doc: str, name: str, radius: float, edges: Union[list[int], str, None] = None, result_name: Optional[str] = None) -> str:
    """Round edges of a solid with `radius` mm. `edges`: list of 1-based edge indices (from list_edges) or an edge selector
    string (see list_edges); default all edges. Modifies the object in place unless `result_name` is given. Fails with a
    clear message if the radius is too large for the geometry."""
    return _j(run_worker("fillet", doc, name=name, radius=radius, edges=edges, result_name=result_name))


@mcp.tool()
def chamfer(doc: str, name: str, size: float, edges: Union[list[int], str, None] = None, result_name: Optional[str] = None) -> str:
    """Bevel edges of a solid by `size` mm (equal distance). Same edge selection rules as fillet."""
    return _j(run_worker("chamfer", doc, name=name, size=size, edges=edges, result_name=result_name))


@mcp.tool()
def delete_object(doc: str, name: str) -> str:
    """Delete one object from the document."""
    return _j(run_worker("delete_object", doc, name=name))


@mcp.tool()
def set_properties(doc: str, name: str, material: Optional[str] = None, part_no: Optional[str] = None, description: Optional[str] = None,
                   supplier: Optional[str] = None, color: Optional[Vec3] = None, quantity: Optional[int] = None,
                   group: Optional[str] = None) -> str:
    """Set metadata on an object. These feed mass_properties, run_fem, bom and the renders. material is one of
    aluminium_6061, steel, stainless_304, pa12, pla, pom (see the freecad://materials resource). color is [r,g,b] 0..1
    (defaults to the material's colour). quantity is used by the BOM."""
    return _j(run_worker("set_properties", doc, name=name, material=material, part_no=part_no, description=description,
                         supplier=supplier, color=color, quantity=quantity, group=group))


@mcp.tool()
def list_faces(doc: str, name: str, selector: str = "all") -> str:
    """List faces of an object with 1-based index, surface type, area, centre, normal (planes) or radius/axis
    (cylinders). Indices are what run_fem and fillet need; they change after any geometry edit, so list again.
    `selector` filters, tokens separated by spaces (all must match): 'normal:+Z' (planar faces with that normal; also -X,
    +Y ...), 'z:max' / 'x:min' / 'y:12.5' (face centre coordinate at the extreme or value), 'type:cylinder|plane',
    'radius:3.3', 'area>50', 'area<10', 'index:1,2'. Example: 'normal:+Z z:max' = the top face."""
    return _j(run_worker("list_faces", doc, name=name, selector=selector))


@mcp.tool()
def list_edges(doc: str, name: str, selector: str = "all") -> str:
    """List edges with 1-based index, curve type, length, midpoint, direction (lines) or radius (circles).
    `selector` tokens (space separated, all must match): 'parallel:X|Y|Z' (straight edges), 'type:line|circle',
    'length>10', 'length<5', 'radius:3.3', 'z:max' / 'x:min' / 'y:5' (midpoint coordinate), 'index:3,4'."""
    return _j(run_worker("list_edges", doc, name=name, selector=selector))


# ============================================================================ import / export
@mcp.tool()
def import_step(doc: str, path: str, name: str, material: Optional[str] = None) -> str:
    """Import a STEP file (absolute path) as a new object `name`."""
    return _j(run_worker("import_step", doc, path=path, name=name, material=material))


@mcp.tool()
def export(doc: str, fmt: Literal["step", "stl", "obj", "3mf", "fcstd"], objects: Optional[list[str]] = None,
           filename: Optional[str] = None) -> str:
    """Export objects (default all solids) to workspace/exports/<doc>/ as STEP, STL, OBJ, 3MF or a FCStd copy.
    Returns the absolute path. For DXF (laser cutting) use export_dxf."""
    p = _out("exports", doc, filename or f"{doc}.{fmt if fmt != 'fcstd' else 'FCStd'}")
    return _j(run_worker("export", doc, fmt=fmt, path=str(p), objects=objects))


# ============================================================================ analysis
@mcp.tool()
def mass_properties(doc: str, objects: Optional[list[str]] = None, material: Optional[str] = None,
                    density_kg_m3: Optional[float] = None) -> str:
    """Volume, mass (g), centre of mass (mm) per object and for the set, plus the combined inertia tensor (g*mm2, about the
    combined centre of mass, world axes). Density comes from each object's material (set_properties) unless `material`
    or `density_kg_m3` overrides it for all objects. Objects without material report volume only."""
    return _j(run_worker("mass_properties", doc, objects=objects, material=material, density_kg_m3=density_kg_m3))


@mcp.tool()
def check_interference(doc: str, objects: Optional[list[str]] = None, tolerance_mm3: float = 0.01) -> str:
    """Pairwise interference test: for every pair of solids whose bounding boxes touch, compute the volume of their
    intersection. Pairs above `tolerance_mm3` are returned with the common volume, overlap centre and size.
    Face contact (zero volume) is not interference. `clean: true` means no pair overlaps."""
    return _j(run_worker("check_interference", doc, objects=objects, tolerance_mm3=tolerance_mm3))


# ============================================================================ seeing the part
@mcp.tool()
def render_view(doc: str, view: Literal["iso", "front", "back", "top", "bottom", "right", "left", "grid"] = "iso",
                objects: Optional[list[str]] = None, size: int = 900, explode: float = 0.0, title: Optional[str] = None) -> list:
    """Render the document (or the listed objects) to a PNG you can look at. views: iso, front (looking along +Y), back,
    top, bottom, right, left, or grid (iso+front+top+right in one image). Objects are coloured by their color/material.
    `explode` (0..1.5) pushes every object away from the assembly centre along the line from the centre to the object's
    own centre, producing an exploded view. The PNG is also saved under workspace/renders/<doc>/."""
    items = _render_items(doc, objects)
    png = R.render_png(items, view, size, explode, title or f"{doc} - {view}")
    p = _out("renders", doc, f"{view}{'_exploded' if explode else ''}.png")
    p.write_bytes(png)
    return [f"saved {p}", Image(data=png, format="png")]


def _render_items(doc, objects):
    d = _out("renders", doc, "stl")
    res = run_worker("export_for_render", doc, out_dir=str(d), objects=objects)
    items = [dict(name=i["name"], tris=R.load_stl(i["stl"]), color=i["color"] or None, material=i["material"] or None) for i in res["items"]]
    if not items:
        raise ToolError("nothing to render")
    return items


# ============================================================================ escape hatch
@mcp.tool()
def run_script(doc: str, code: str) -> str:
    """Escape hatch: run arbitrary FreeCAD Python in the worker on `doc` (saved afterwards). Predefined: App (=FreeCAD), Part,
    V (Vector), doc, result (a dict you can fill; it is returned), add_shape(doc, name, shape, material=None). print()
    output is returned. Use only when no dedicated tool fits; geometry made here should go in via add_shape so other tools
    see it."""
    return _j(run_worker("run_script", doc, code=code))


# ============================================================================ resources and prompts
@mcp.resource("freecad://documents")
def documents_resource() -> str:
    """Workspace documents (JSON list). A RESOURCE is read-only context the client can attach, unlike a tool it has no side effects."""
    return list_documents()


@mcp.resource("freecad://materials")
def materials_resource() -> str:
    """Built-in materials with density (kg/m3), Young's modulus (MPa), Poisson ratio and yield strength (MPa)."""
    return _j(MATERIALS)


@mcp.prompt()
def design_and_validate_bracket(load_n: float = 500.0, material: str = "aluminium_6061") -> str:
    """A PROMPT is a reusable instruction template the user selects in the client (e.g. as a slash command)."""
    return f"""Design and validate an L-bracket with the FreeCAD tools, then report.
1. new_document 'bracket'. Model a 60x40 mm L-profile, 5 mm thick, 30 mm wide (extrude_polygon in the XZ plane),
   two 6.6 mm through holes in the base (cylinders + boolean cut), fillet the inner corner (list_edges first).
2. set_properties material={material}; mass_properties.
3. Add an M6 bolt (cylinder shaft + head) in a hole and check_interference.
4. run_fem: fix the base underside, apply {load_n} N downward on the vertical leg's outer face. Target yield safety
   factor >= 2. If it fails, thicken or add fillets and repeat from step 2.
5. render_view grid, make_drawing, bom, export step. Look at every render before declaring success, and state
   honestly any check that failed."""


if __name__ == "__main__":
    mcp.run()          # stdio transport: reads JSON-RPC from stdin, writes to stdout
