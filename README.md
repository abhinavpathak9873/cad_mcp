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

## Install and use

You need three things on the machine that runs the server: FreeCAD 1.x (headless `freecadcmd` is enough), Python 3.10+
and [`uv`](https://docs.astral.sh/uv/). The AI client (Claude Code, Codex, Claude Desktop, Cursor, ...) starts the server
itself as a subprocess and talks to it over stdin/stdout, so you never run `server.py` by hand in normal use.

### 1. Get the server and find `freecadcmd`

```
git clone https://github.com/abhinavpathak9873/cad_mcp
cd cad_mcp
uv sync                      # creates .venv with the mcp SDK, numpy, matplotlib
```

Find the headless FreeCAD binary for your OS; this path goes into `FREECADCMD` below:

- Linux, distro package: `freecadcmd` (often already on PATH; check with `which freecadcmd`).
- Linux, AppImage: run `./FreeCAD_*.AppImage --appimage-extract` once, then use `<dir>/squashfs-root/usr/bin/freecadcmd`.
- macOS: `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`.
- Windows: `C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe` (adjust the version folder).

Check FreeCAD and the server work before wiring up a client:

```
export FREECADCMD=/path/to/freecadcmd
"$FREECADCMD" -c "import FreeCAD; print(FreeCAD.Version())"
uv run pytest -q             # real FreeCAD runs, takes a few minutes
```

Use absolute paths everywhere below. Clients start the server from their own working directory, so relative paths break.

### 2a. Claude Code

One command (user scope = available in every project; use `--scope project` to write a shareable `.mcp.json` instead):

```
claude mcp add cad_mcp --scope user \
  -e FREECADCMD=/path/to/freecadcmd \
  -- uv --directory /abs/path/to/cad_mcp run server.py
```

Then check it: `claude mcp list` should show `cad_mcp ... Connected`, and inside a session `/mcp` lists its tools.
FEM and drawings can take longer than the default tool timeout, so start Claude Code with a longer one:

```
MCP_TOOL_TIMEOUT=300000 claude      # milliseconds
```

Project-scoped alternative: copy `.mcp.json.example` to `.mcp.json` in your project root and fix the two paths. Claude Code
asks once whether to trust project MCP servers.

Remove it again with `claude mcp remove cad_mcp`.

### 2b. Codex (OpenAI Codex CLI / IDE extension)

One command:

```
codex mcp add cad_mcp --env FREECADCMD=/path/to/freecadcmd \
  -- uv --directory /abs/path/to/cad_mcp run server.py
```

or add it by hand to `~/.codex/config.toml` (the CLI and the IDE extension share this file):

```toml
[mcp_servers.cad_mcp]
command = "uv"
args = ["--directory", "/abs/path/to/cad_mcp", "run", "server.py"]
env = { FREECADCMD = "/path/to/freecadcmd" }
startup_timeout_sec = 30
tool_timeout_sec = 300        # FEM and drawings can exceed the 60 s default
```

Check with `codex mcp list`, and inside a Codex session with `/mcp`. Remove with `codex mcp remove cad_mcp`.

### 2c. Claude Desktop

Settings > Developer > Edit Config opens `claude_desktop_config.json`
(macOS `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\`). Add:

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

Restart Claude Desktop; the tools appear under the tools icon in the chat box. If `uv` is not found, put the absolute path
to `uv` in `command` (`which uv`), because the desktop app does not load your shell PATH.

### 2d. Any other MCP client (Cursor, VS Code, Windsurf, Zed, ...)

They all use the same three fields: command `uv`, args `--directory /abs/path/to/cad_mcp run server.py`, env
`FREECADCMD`. Cursor reads the same JSON shape as above from `.cursor/mcp.json`; VS Code uses `.vscode/mcp.json` with the
top-level key `servers` instead of `mcpServers`.

### 3. Poke it without any AI client

The MCP Inspector is a small web UI that acts as a client, so you can list tools and call them by hand:

```
npx @modelcontextprotocol/inspector uv --directory /abs/path/to/cad_mcp run server.py
```

`client_raw.py` does the same from plain Python with no SDK and prints every JSON-RPC
message, which is the clearest way to see what the protocol actually looks like.

### 4. First prompt to try

> Using cad_mcp: make an L-bracket, 60 x 40 mm base and 50 mm wall, 5 mm thick aluminium 6061, two M6 clearance holes in
> the base, 3 mm fillet on the inside corner. Render it and look at it, give me the mass, then run FEM with the wall's
> back face fixed and 300 N down on the base's top face. If the yield safety factor is under 3, thicken it and rerun.
> Finish with a drawing and a STEP export.

### Settings

Environment variables read by the server:

- `FREECADCMD`: path to `freecadcmd` (default: `freecadcmd` on PATH).
- `FREECAD_MCP_WORKSPACE`: where documents, renders and exports are written (default `./workspace` inside the repo).
- `FREECAD_MCP_TIMEOUT`: seconds allowed per FreeCAD call (default 240).
- `FREECAD_MCP_LAUNCHER`: command prefix for each FreeCAD subprocess, e.g. `systemd-run --user --scope -p MemoryMax=8G`
  to cap memory on a shared machine.

### Troubleshooting

- Server shows as failed or disconnected: run `uv --directory /abs/path/to/cad_mcp run server.py` yourself. It should sit
  silently waiting for input (Ctrl-C to quit). Any traceback is the real error.
- Tools time out: raise the client's tool timeout (see above) and `FREECAD_MCP_TIMEOUT`, or use a coarser FEM mesh.
- `freecadcmd` not found: `FREECADCMD` must be an absolute path to the binary, not to the AppImage file itself.
- Never `print()` to stdout inside the server: stdout is the protocol channel. Logs go to stderr.

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
