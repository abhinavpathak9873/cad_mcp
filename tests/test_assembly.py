import json, math, os, pytest
J = json.loads

def plate(s, doc):
    s.new_document(doc)
    s.add_primitive(doc, "plate", "box", {"length": 40, "width": 30, "height": 8}, material="aluminium_6061")
    s.hole(doc, "plate", [20, 15, 8], [0, 0, -1], 6.6)

def test_fasteners_mate_clearance(srv):
    s = srv; doc = "t_asm"
    plate(s, doc)
    b = J(s.add_fastener(doc, "bolt", "shcs", "M6", length=20, position=[100, 100, 100], direction=[0, 0, -1]))
    assert b["meta"]["part_no"] == "ISO 4762 M6x20"
    # shank cylinder of the bolt and the hole's cylindrical face
    bf = J(s.list_faces(doc, "bolt", "type:cylinder radius:3"))["faces"]
    hf = J(s.list_faces(doc, "plate", "type:cylinder radius:3.3"))["faces"]
    assert len(bf) == 1 and len(hf) == 1
    s.mate_coaxial(doc, "bolt", bf[0]["index"], "plate", hf[0]["index"])
    # seat the head underside (the annulus facing the plate) on the plate top
    hu = J(s.list_faces(doc, "bolt", "normal:-Z area>40 type:plane"))["faces"]
    top = J(s.list_faces(doc, "plate", "normal:+Z z:max"))["faces"]
    assert len(hu) == 1 and len(top) == 1
    s.mate_faces(doc, "bolt", hu[0]["index"], "plate", top[0]["index"])
    nut = s.add_fastener(doc, "nut", "hex_nut", "M6", position=[0, 0, 0])
    s.add_fastener(doc, "washer", "washer", "M6", position=[60, 0, 0])
    r = J(s.check_interference(doc, ["plate", "bolt"]))
    assert r["clean"], r                      # M6 shank (6.0) in a 6.6 hole: clear
    s.add_primitive(doc, "post", "box", {"length": 5, "width": 5, "height": 20}, position=[28.5, 12.5, 8])
    c = J(s.check_clearance(doc, ["plate", "bolt", "post"], 5.0))
    d = {(p["a"], p["b"]): p["min_distance_mm"] for p in c["pairs"]}
    assert d[("plate", "bolt")] == pytest.approx(0.0, abs=1e-6)        # head seated on the plate: contact
    assert d[("bolt", "post")] == pytest.approx(3.5, abs=0.01)         # head edge x=25 to post x=28.5
    # exploded render + section render
    out = s.render_view(doc, "iso", explode=0.6)
    assert out[1].data[:4] == b"\x89PNG"
    sec = s.section_view(doc, "y", 15, "above")
    assert sec[1].data[:4] == b"\x89PNG"

def test_mate_faces_and_library(srv, tmp_path):
    s = srv; doc = "t_mate"
    s.new_document(doc)
    s.add_primitive(doc, "base", "box", {"length": 20, "width": 20, "height": 10})
    s.add_primitive(doc, "cube", "box", {"length": 6, "width": 6, "height": 6}, position=[50, 50, 50])
    s.mate_faces(doc, "cube", "normal:-Z z:min", "base", "normal:+Z z:max", offset=0.0)
    o = [x for x in J(s.list_objects(doc))["objects"] if x["name"] == "cube"][0]
    assert o["bbox"]["min"] == pytest.approx([7, 7, 10], abs=1e-3)
    assert J(s.check_interference(doc))["clean"]
    # library: export a part, put it into a library folder, list + insert
    p = J(s.export(doc, "step", ["cube"], filename="cube.step"))["path"]
    lib = s.LIBRARY; lib.mkdir(exist_ok=True)
    import shutil; shutil.copy(p, lib / "t_cube.step")
    try:
        assert "t_cube.step" in J(s.list_library("cube"))
        r = J(s.insert_library_part(doc, "t_cube.step", "lib_cube", position=[100, 0, 0]))
        assert r["bbox"]["min"][0] == pytest.approx(107, abs=1e-3)   # exported at x=7, translate adds 100
    finally:
        (lib / "t_cube.step").unlink()

def test_undo_history_dxf(srv):
    s = srv; doc = "t_undo"
    s.new_document(doc)
    s.add_primitive(doc, "a", "box", {"length": 10, "width": 10, "height": 10})
    s.add_primitive(doc, "b", "box", {"length": 5, "width": 5, "height": 5})
    assert len(J(s.list_objects(doc))["objects"]) == 2
    s.undo(doc)
    assert [o["name"] for o in J(s.list_objects(doc))["objects"]] == ["a"]
    hist = J(s.object_history(doc, "a"))
    assert hist and hist[0]["op"] == "add_primitive"
    assert J(s.document_info(doc))["undo_depth"] >= 1
    # DXF of a plate with a hole: outline 4 lines + 1 circle
    s.hole(doc, "a", [5, 5, 10], [0, 0, -1], 4.0)
    face = J(s.list_faces(doc, "a", "normal:+Z z:max"))["faces"][0]["index"]
    d = J(s.export_dxf(doc, "a", face=face))
    assert d["circles"] == 1 and d["entities"] == 5
    txt = open(d["path"]).read()
    assert "CIRCLE" in txt and txt.count("LINE") >= 4
    sec = J(s.export_dxf(doc, "a", plane="z", offset=3.0))
    assert sec["circles"] == 1
