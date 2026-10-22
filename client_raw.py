#!/usr/bin/env python3
"""A raw MCP client: no SDK, just JSON-RPC 2.0 lines over the server's stdin/stdout.

Shows the whole protocol: initialize -> notifications/initialized -> tools/list, resources/list, prompts/list ->
resources/read, prompts/get -> tools/call. Then runs an end-to-end engineering loop on an L-bracket:
model, fillet, mass, fastener + interference, FEM, drawing, renders, section, DXF, BOM, STEP export.

    uv run python client_raw.py [--out examples] [--quiet]

Every request/response is printed in abbreviated form (images are saved to --out instead of printed).
"""
import argparse, base64, json, os, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class MCPClient:
    def __init__(self, workspace, out, verbose=True):
        env = dict(os.environ, FREECAD_MCP_WORKSPACE=str(workspace))
        self.p = subprocess.Popen([sys.executable, str(ROOT / "server.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, env=env, cwd=ROOT)
        self.n = 0; self.out = Path(out); self.out.mkdir(parents=True, exist_ok=True); self.verbose = verbose

    def _send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n"); self.p.stdin.flush()      # one JSON object per line

    def _show(self, arrow, obj, limit=230):
        if self.verbose:
            t = json.dumps(obj)
            print(f"  {arrow} {t if len(t) <= limit else t[:limit] + ' ...'}")

    def request(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self._show("->", msg); self._send(msg)
        while True:                              # skip any server notifications until our response arrives
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("server closed the pipe")
            r = json.loads(line)
            if r.get("id") == self.n:
                break
        if "error" in r:
            raise RuntimeError(f"{method}: {r['error']}")
        self._show("<-", self._strip_images(r)); return r["result"]

    def notify(self, method):
        msg = {"jsonrpc": "2.0", "method": method}
        self._show("->", msg); self._send(msg)

    @staticmethod
    def _strip_images(r):
        r = json.loads(json.dumps(r))
        for c in r.get("result", {}).get("content", []) if isinstance(r.get("result"), dict) else []:
            if c.get("type") == "image":
                c["data"] = f"<{len(c['data'])} base64 chars>"
        return r

    def call(self, tool, save_image=None, **args):
        """tools/call; returns (parsed JSON of the first text block, image bytes or None)."""
        res = self.request("tools/call", {"name": tool, "arguments": args})
        if res.get("isError"):
            raise RuntimeError(f"{tool} failed: {res['content'][0]['text']}")
        text = next((c["text"] for c in res["content"] if c["type"] == "text"), "")
        img = next((base64.b64decode(c["data"]) for c in res["content"] if c["type"] == "image"), None)
        if img and save_image:
            (self.out / save_image).write_bytes(img)
        try:
            return json.loads(text), img
        except ValueError:
            return text, img

    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=20)


def run_demo(out, workspace=None, verbose=True):
    workspace = workspace or tempfile.mkdtemp(prefix="cad_mcp_demo_")
    c = MCPClient(workspace, out, verbose)
    R = {}
    step = lambda s: print(f"\n== {s}") if verbose else None

    step("1. handshake and discovery")
    init = c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "client_raw", "version": "0"}})
    c.notify("notifications/initialized")
    tools = c.request("tools/list")["tools"]
    res = c.request("resources/list")["resources"]
    prompts = c.request("prompts/list")["prompts"]
    c.request("resources/read", {"uri": "freecad://materials"})
    c.request("prompts/get", {"name": "design_and_validate_bracket", "arguments": {"load_n": "300"}})
    R.update(server=init["serverInfo"]["name"], n_tools=len(tools), n_resources=len(res), n_prompts=len(prompts),
             tool_names=[t["name"] for t in tools])

    step("2. model an L-bracket: 60x40 L profile, 5 mm thick, 30 wide, two M6 clearance holes")
    D = "bracket"
    c.call("new_document", name=D)
    c.call("extrude_polygon", doc=D, name="body", points=[[0, 0], [60, 0], [60, 5], [5, 5], [5, 40], [0, 40]], height=30, plane="XZ", material="aluminium_6061")
    for i, y in enumerate((8, 22)):
        c.call("add_primitive", doc=D, name=f"drill{i}", shape="cylinder", dimensions={"radius": 3.3, "height": 12}, position=[45, y, -3])
    c.call("boolean", doc=D, operation="cut", base="body", tools=["drill0", "drill1"], result_name="bracket")
    edges, _ = c.call("list_edges", doc=D, name="bracket", selector="parallel:Y x:min z:min")
    # the inner corner is the Y-parallel edge at x=5,z=5; the selector above lists the outer one, so ask for the inner one by position
    inner, _ = c.call("list_edges", doc=D, name="bracket", selector="parallel:Y length>25")
    inner_idx = [e["index"] for e in inner["edges"] if abs(e["center"][0] - 5) < 1e-3 and abs(e["center"][2] - 5) < 1e-3]
    fil, _ = c.call("fillet", doc=D, name="bracket", radius=4.0, edges=inner_idx)
    R["fillet_valid"] = fil["valid"]; R["bracket_volume_mm3"] = fil["volume_mm3"]
    c.call("set_properties", doc=D, name="bracket", part_no="BR-001", description="L bracket 60x40x30", quantity=1)

    step("3. mass and fasteners")
    mass, _ = c.call("mass_properties", doc=D)
    R["bracket_mass_g"] = mass["total_mass_g"]
    for i, y in enumerate((8, 22)):
        c.call("add_fastener", doc=D, name=f"bolt{i}", kind="shcs", size="M6", length=16, position=[45, y, 5], direction=[0, 0, -1])
    c.call("add_primitive", doc=D, name="bad_bolt", shape="cylinder", dimensions={"radius": 3.6, "height": 14}, position=[45, 15, -4])  # deliberately too fat? it sits between holes
    c.call("transform", doc=D, name="bad_bolt", translate=[0, -7, 0])                  # now inside hole 0 -> must be reported
    intf, _ = c.call("check_interference", doc=D)
    R["interference_pairs_with_bad_bolt"] = [(h["a"], h["b"]) for h in intf["interferences"]]
    R["interference_volume_mm3"] = intf["interferences"][0]["common_volume_mm3"] if intf["interferences"] else 0
    c.call("delete_object", doc=D, name="bad_bolt")
    intf2, _ = c.call("check_interference", doc=D)
    R["interference_clean_after_fix"] = intf2["clean"]
    clr, _ = c.call("check_clearance", doc=D, objects=["bracket", "bolt0"], report_below_mm=1.0)
    R["clearance_bolt_to_bracket_mm"] = clr["smallest_mm"]

    step("4. look at it")
    c.call("render_view", doc=D, view="grid", save_image="bracket_grid.png")
    c.call("render_view", doc=D, view="iso", explode=0.7, save_image="bracket_exploded.png")
    c.call("section_view", doc=D, axis="y", offset=8, remove="above", save_image="bracket_section.png")

    step("5. FEM: bolted through the holes, 300 N pushing the vertical leg")
    fem, _ = c.call("run_fem", doc=D, name="bracket", fixed_faces="type:cylinder radius:3.3",
                    loads=[{"faces": "normal:-X x:min", "force_n": 300, "direction": [1, 0, 0]}], mesh_size=4.0, save_image="bracket_fem_vonmises.png")
    (c.out / "fem_result.json").write_text(json.dumps(fem, indent=1))
    R["fem"] = {k: fem[k] for k in ("max_von_mises_mpa", "max_von_mises_mpa_raw", "max_displacement_mm", "safety_factor_yield", "nodes")}

    step("6. drawing, BOM, exports")
    dr, img = c.call("make_drawing", doc=D, objects=["bracket"], title="L bracket", author="cad_mcp", save_image="bracket_drawing.png")
    for ext in ("svg", "pdf"):
        (c.out / f"bracket_drawing.{ext}").write_bytes(Path(dr[ext]).read_bytes())
    R["drawing_scale"] = dr["scale"]
    bom, _ = c.call("bom", doc=D)
    (c.out / "bracket_bom.md").write_text(Path(bom["markdown"]).read_text()); (c.out / "bracket_bom.csv").write_text(Path(bom["csv"]).read_text())
    R["bom_lines"] = bom["line_items"]; R["bom_total_mass_g"] = bom["total_mass_g"]
    step_p, _ = c.call("export", doc=D, fmt="step", filename="bracket_assembly.step")
    (c.out / "bracket_assembly.step").write_bytes(Path(step_p["path"]).read_bytes())
    R["step_bytes"] = step_p["bytes"]
    bottom, _ = c.call("list_faces", doc=D, name="bracket", selector="normal:-Z z:min")
    dxf, _ = c.call("export_dxf", doc=D, name="bracket", face=bottom["faces"][0]["index"], filename="bracket_base.dxf")
    (c.out / "bracket_base.dxf").write_bytes(Path(dxf["path"]).read_bytes())
    R["dxf_circles"] = dxf["circles"]
    c.close()
    return R


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=str(ROOT / "examples")); ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    r = run_demo(a.out, verbose=not a.quiet)
    print("\nSUMMARY", json.dumps({k: v for k, v in r.items() if k != "tool_names"}, indent=1))
