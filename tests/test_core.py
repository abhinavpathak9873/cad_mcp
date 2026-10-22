"""Modelling, export, render, mass and interference through the tool functions (same code path as MCP calls)."""
import json, os
import pytest
from mcp.server.fastmcp.exceptions import ToolError

J = json.loads
DOC = "t_core"

def bracket(s, doc):
    s.new_document(doc)
    s.extrude_polygon(doc, "bracket", [[0, 0], [60, 0], [60, 5], [5, 5], [5, 40], [0, 40]], 30, plane="XZ", material="aluminium")
    for i, y in enumerate((8, 22)):
        s.add_primitive(doc, f"hole{i}", "cylinder", {"radius": 3.3, "height": 12}, position=[40, y, -3])
    s.boolean(doc, "cut", "bracket", ["hole0", "hole1"], "bracket")
    e = J(s.list_edges(doc, "bracket", "parallel:Y length>25 x:min"))["edges"]
    return e

def test_modelling_and_mass(srv):
    s = srv
    e = bracket(s, DOC)
    assert e, "selector found no Y-parallel edges"
    r = J(s.list_objects(DOC))["objects"][0]
    # L profile area = 60*5 + 35*5 = 475 mm2, times 30 wide, minus two holes of r=3.3 through 5 mm
    import math
    expect = 475 * 30 - 2 * math.pi * 3.3 ** 2 * 5
    assert r["volume_mm3"] == pytest.approx(expect, rel=1e-3)
    f = J(s.fillet(DOC, "bracket", 4.0, "parallel:Y x:min z:min"))
    assert f["valid"] and f["edges_used"]
    m = J(s.mass_properties(DOC))
    assert 35 < m["total_mass_g"] < 45          # ~ 0.0027 g/mm3 * 13.9e3 mm3 = 37 g
    assert len(m["inertia_tensor_g_mm2"]) == 3
    faces = J(s.list_faces(DOC, "bracket", "normal:+Z z:max"))["faces"]
    assert faces and faces[0]["normal"][2] > 0.99
    with pytest.raises(ToolError):
        s.fillet(DOC, "bracket", 50.0)

def test_interference_and_transform(srv):
    s = srv
    doc = "t_intf"
    bracket(s, doc)
    s.add_primitive(doc, "bolt_ok", "cylinder", {"radius": 3.0, "height": 15}, position=[40, 8, -5], material="steel")
    s.add_primitive(doc, "bolt_tight", "cylinder", {"radius": 3.6, "height": 15}, position=[40, 22, -5], material="steel")
    r = J(s.check_interference(doc))
    pairs = {(h["a"], h["b"]) for h in r["interferences"]}
    assert pairs == {("bracket", "bolt_tight")} and not r["clean"]
    s.transform(doc, "bolt_tight", translate=[0, 30, 0])
    assert J(s.check_interference(doc))["clean"]

def test_export_and_render(srv):
    s = srv
    doc = "t_exp"
    bracket(s, doc)
    for fmt in ("step", "stl", "obj", "3mf"):
        p = J(s.export(doc, fmt))["path"]
        assert os.path.getsize(p) > 500, fmt
    out = s.render_view(doc, "grid")
    assert out[1].data[:8] == b"\x89PNG\r\n\x1a\n" and len(out[1].data) > 20000

def test_undo_free_script_and_resources(srv):
    s = srv
    doc = "t_script"
    s.new_document(doc)
    r = J(s.run_script(doc, "b = Part.makeBox(10,10,10)\nadd_shape(doc,'cube',b)\nprint('hi')\nresult['v']=b.Volume"))
    assert r["stdout"].strip() == "hi" and r["result"]["v"] == pytest.approx(1000)
    assert "t_script" in s.list_documents()
    assert "aluminium_6061" in s.materials_resource()
