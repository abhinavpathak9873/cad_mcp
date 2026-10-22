import json, math, os, pytest
J = json.loads

def test_sketch_pad_pocket_features(srv):
    s = srv; doc = "t_feat"
    s.new_document(doc)
    # constrained rectangle 40x20 with a 6 mm circle hole, padded 10
    r = J(s.create_sketch(doc, "sk", "XY",
        geometry=[{"type": "polyline", "points": [[0, 0], [40, 0], [40, 20], [0, 20]]}, {"type": "circle", "center": [20, 10], "radius": 3}],
        constraints=[{"type": "horizontal", "geo": 0}, {"type": "length", "geo": 0, "value": 40}]))
    assert r["closed_wires"] == 2 and r["open_wires"] == 0
    p = J(s.pad(doc, "sk", 10, "plate", material="steel"))
    assert p["volume_mm3"] == pytest.approx((40 * 20 - math.pi * 9) * 10, rel=1e-4)
    # counterbore hole from the top face
    h = J(s.hole(doc, "plate", [8, 10, 10], [0, 0, -1], 4.0, kind="counterbore", cbore_diameter=8.0, cbore_depth=3.0))
    assert h["volume_mm3"] == pytest.approx((40 * 20 - math.pi * 9) * 10 - math.pi * 4 * 10 - math.pi * (16 - 4) * 3 + 0, rel=2e-3)
    # pocket from a sketch on the top face
    s.create_sketch(doc, "sk2", "XY", offset=10, geometry=[{"type": "polyline", "points": [[30, 5], [36, 5], [36, 15], [30, 15]]}])
    q = J(s.pocket(doc, "sk2", "plate", depth=4))
    assert q["volume_mm3"] == pytest.approx(h["volume_mm3"] - 60 * 4, rel=1e-3)
    # mirror + linear pattern + polar pattern
    s.add_primitive(doc, "peg", "cylinder", {"radius": 2, "height": 5}, position=[5, 5, 0])
    L = J(s.linear_pattern(doc, "peg", [1, 0, 0], 3, 10, "pegs", fuse=True))
    assert L["volume_mm3"] == pytest.approx(3 * math.pi * 4 * 5, rel=1e-3)
    s.add_primitive(doc, "pin", "cylinder", {"radius": 1, "height": 5}, position=[10, 0, 0])
    P = J(s.polar_pattern(doc, "pin", 6, "ring"))
    assert len(P["created"]) == 5
    M = J(s.mirror(doc, "peg", "YZ", "peg_m"))
    assert M["bbox"]["min"][0] == pytest.approx(-7)

def test_revolve_loft_sweep_shell(srv):
    s = srv; doc = "t_feat2"
    s.new_document(doc)
    s.create_sketch(doc, "prof", "XY", geometry=[{"type": "polyline", "points": [[0, 0], [10, 0], [10, 30], [0, 30]]}])
    v = J(s.revolve(doc, "prof", "shaft", axis_point=[0, 0, 0], axis_dir=[0, 1, 0]))
    assert v["volume_mm3"] == pytest.approx(math.pi * 100 * 30, rel=1e-3)
    s.create_sketch(doc, "a", "XY", geometry=[{"type": "polyline", "points": [[0, 0], [20, 0], [20, 20], [0, 20]]}])
    s.create_sketch(doc, "b", "XY", offset=30, geometry=[{"type": "polyline", "points": [[5, 5], [15, 5], [15, 15], [5, 15]]}])
    lo = J(s.loft(doc, ["a", "b"], "loft", ruled=True))
    assert 4000 < lo["volume_mm3"] < 12000 and lo["valid"]
    s.add_primitive(doc, "box", "box", {"length": 30, "width": 30, "height": 30}, position=[100, 0, 0])
    sh = J(s.shell(doc, "box", 3, "normal:+Z z:max"))
    assert sh["volume_mm3"] == pytest.approx(30 ** 3 - 24 ** 2 * 27, rel=1e-3)
    s.create_sketch(doc, "pp", "XY", geometry=[{"type": "circle", "center": [0, 0], "radius": 2}], offset=0)
    sw = J(s.sweep(doc, "pp", "pipe", path_points=[[0, 0, 0], [0, 0, 20], [20, 0, 40]]))
    assert sw["volume_mm3"] > math.pi * 4 * 40 * 0.9

def test_drawing_and_bom(srv):
    s = srv; doc = "t_draw"
    s.new_document(doc)
    s.extrude_polygon(doc, "bracket", [[0, 0], [60, 0], [60, 5], [5, 5], [5, 40], [0, 40]], 30, plane="XZ", material="aluminium_6061",)
    s.add_primitive(doc, "h", "cylinder", {"radius": 3.3, "height": 12}, position=[40, 15, -3])
    s.boolean(doc, "cut", "bracket", ["h"], "bracket")
    s.set_properties(doc, "bracket", part_no="BR-001", description="L bracket", quantity=2)
    s.add_primitive(doc, "bolt", "cylinder", {"radius": 3, "height": 16}, position=[40, 15, -8], material="steel")
    s.set_properties(doc, "bolt", part_no="ISO4762-M6x16", description="SHCS M6x16", supplier="generic")
    out = s.make_drawing(doc, title="L bracket", author="test")
    d = J(out[0])
    for k in ("svg", "pdf", "png"):
        assert os.path.getsize(d[k]) > 2000
    assert d["hole_dimensions"] >= 1 and d["overall"]["top"][0] == pytest.approx(60, rel=1e-3)
    assert "<svg" in open(d["svg"]).read(500)
    b = J(s.bom(doc))
    assert b["line_items"] == 2 and b["total_parts"] == 3
    assert os.path.exists(b["csv"]) and "BR-001" in open(b["markdown"]).read()
