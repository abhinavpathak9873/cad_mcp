# cad_mcp - a FreeCAD engineering MCP server

`cad_mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server that lets an AI client (Claude Code,
Claude Desktop, any MCP client) use FreeCAD as an engineering tool. It runs FreeCAD headless, so it works on servers and
in CI. The model can build parts, look at them, check mass and interference, run a finite element analysis, and produce
drawings, a bill of materials and export files, and it gets numbers back at every step so it can verify its own work.

It is written to be readable: `server.py` is one file with the tool declarations and comments for someone learning MCP.

## What MCP is, in one minute

- An MCP server is a program the client starts as a child process. They talk JSON-RPC 2.0 over the server's stdin and stdout.
- The server advertises three kinds of things. Tools are functions the model can call (typed parameters, the docstring is
  the description the model reads). Resources are read-only data addressed by URI. Prompts are reusable prompt templates.
- This server has about 40 tools, resources `freecad://documents` and `freecad://materials`, and the prompt
  `design_and_validate_bracket`.
- `client_raw.py` talks to the server with no SDK at all, so you can see every message on the wire.

## Architecture

```
 MCP client (Claude)          server.py (FastMCP, stdio)                 freecadcmd (one process per call)
 ------------------          ---------------------------                 ---------------------------------
  tools/call  ----JSON-RPC--->  validate args, snapshot doc  --job.json-->  cad_mcp/core.py: open workspace/<doc>.FCStd
                                                                            run ONE registered op (ops_*.py)
  <--text + PNG image---------  format result              <--result.json--  save .FCStd, write result JSON
                                render.py: STL -> PNG (numpy + matplotlib, in the server process)
```

- Stateless on purpose: state lives in `workspace/<doc>.FCStd`. A crashed or hung FreeCAD never takes the session down.
- Before every modifying call the previous file is copied to `workspace/.history/<doc>/` (used for undo).
- Renders: FreeCAD tessellates each object to STL; the server projects the triangles, draws sharp edges, shades and returns
  a PNG as MCP image content, so the model can actually look at its part.

## Setup

Requirements: FreeCAD 1.x with the FEM workbench (CalculiX and Gmsh come with the FreeCAD AppImage), Python 3.10+, `uv`.

```
git clone https://github.com/abhinavpathak9873/cad_mcp && cd cad_mcp
uv sync
export FREECADCMD=/path/to/freecadcmd      # default: freecadcmd on PATH
uv run pytest -q                           # runs the real FreeCAD tests (a few minutes)
```

Environment variables: `FREECADCMD`, `FREECAD_MCP_WORKSPACE` (default `./workspace`), `FREECAD_MCP_TIMEOUT` (seconds per call,
default 240), `FREECAD_MCP_LAUNCHER` (command prefix for the subprocess, e.g. `systemd-run --user --scope -p MemoryMax=8G`).

### Register with Claude Code

```
claude mcp add cad_mcp -e FREECADCMD=/path/to/freecadcmd -- uv --directory /abs/path/to/cad_mcp run server.py
```

or commit a `.mcp.json` next to your project (see `.mcp.json.example`).

### Register with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cad_mcp": {
      "command": "uv",
      "args": ["--directory", "/abs/path/to/cad_mcp", "run", "server.py"],
      "env": {"FREECADCMD": "/path/to/freecadcmd"}
    }
  }
}
```

## Tool reference

Conventions: millimetres, degrees, +Z up. Faces and edges are addressed by 1-based index (from `list_faces` /
`list_edges`) or by a selector string such as `normal:+Z z:max` (top face) or `parallel:Y length>25`. Indices change after
any geometry edit, so list again.

Documents and inspection
- `new_document`, `list_documents`, `list_objects` (volume, bbox, centre of mass, validity, metadata)
- `list_faces`, `list_edges` (indices with centres, normals, radii; selectors)
- `set_properties` (material, part number, description, supplier, colour, quantity)

Modelling
- `add_primitive` (box, cylinder, sphere, cone, torus with placement), `extrude_polygon`
- `create_sketch` (lines, arcs, circles, polylines with Sketcher constraints), `pad`, `pocket`, `revolve`, `loft`, `sweep`
- `hole` (simple, counterbore, countersink), `shell`, `fillet`, `chamfer`
- `boolean` (cut, fuse, common), `transform`, `mirror`, `linear_pattern`, `polar_pattern`
- `delete_object`, `import_step`

Engineering checks
- `mass_properties` (mass, centre of mass, inertia tensor)
- `check_interference` (pairwise common volume)
- `run_fem` (linear static, Gmsh + CalculiX: fixtures, multiple loads, von Mises, displacement, yield safety factor, plot)

Seeing and documenting
- `render_view` (iso, front, back, top, bottom, left, right, grid, exploded)
- `make_drawing` (third-angle sheet, hidden lines, dimensions, hole callouts, title block; SVG, PDF, PNG)
- `bom` (CSV and Markdown), `export` (STEP, STL, OBJ, 3MF, FCStd)

Escape hatch
- `run_script` runs arbitrary FreeCAD Python on a document.

Resources: `freecad://documents`, `freecad://materials`. Prompt: `design_and_validate_bracket`.

Built-in materials: aluminium_6061, steel, stainless_304, pa12, pla, pom (handbook values, not certified).

## Honest limits

- Headless only: there is no GUI, and the render is a lightweight software renderer, not a ray tracer.
- Stateless subprocess per call: roughly 1-2 s overhead per tool call.
- FEM is linear static, small displacement, isotropic material, tetrahedral mesh of one solid. Stress at fixed or loaded
  face edges is a numerical singularity; the tool reports a value that excludes it and the raw peak.
- Objects are stored as plain shapes (not a parametric feature tree), so earlier steps cannot be edited retroactively; use
  undo or rebuild.
- Interference uses exact boolean intersection and can be slow on complex assemblies.

## Credits

Tool design ideas were informed by existing community FreeCAD MCP servers: neka-nat/freecad-mcp, bonninr/freecad_mcp and
tessalabs-space/freecad-mcp. No code was copied.

Licence: MIT.
