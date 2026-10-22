"""End-to-end through the real stdio JSON-RPC protocol (client_raw.py), asserting engineering numbers."""
import math, os, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def test_bracket_end_to_end(tmp_path):
    import client_raw
    r = client_raw.run_demo(tmp_path / "out", workspace=tmp_path / "ws", verbose=False)
    assert r["server"] == "cad_mcp" and r["n_tools"] >= 40 and r["n_resources"] == 2 and r["n_prompts"] == 1
    # L profile 475 mm2 x 30 mm minus two 6.6 mm holes (5 mm thick), plus the 4 mm inner fillet (adds material)
    base = 475 * 30 - 2 * math.pi * 3.3 ** 2 * 5
    fillet_add = (4 ** 2 - math.pi * 4 ** 2 / 4) * 30
    assert r["bracket_volume_mm3"] == pytest.approx(base + fillet_add, rel=2e-3)
    assert r["bracket_mass_g"] == pytest.approx(r["bracket_volume_mm3"] * 2.7e-3, rel=1e-3)
    assert ("bracket", "bad_bolt") in [tuple(p) for p in r["interference_pairs_with_bad_bolt"]]
    assert r["interference_volume_mm3"] > 1 and r["interference_clean_after_fix"]
    assert r["clearance_bolt_to_bracket_mm"] == pytest.approx(0.0, abs=1e-6) or r["clearance_bolt_to_bracket_mm"] < 0.35
    f = r["fem"]
    assert 5 < f["max_von_mises_mpa"] < 500 and 0 < f["max_displacement_mm"] < 5 and f["safety_factor_yield"] > 0.5
    assert r["bom_lines"] == 2 + 0 or r["bom_lines"] >= 2
    out = tmp_path / "out"
    for name in ("bracket_grid.png", "bracket_section.png", "bracket_fem_vonmises.png", "bracket_drawing.png", "bracket_drawing.pdf",
                 "bracket_drawing.svg", "bracket_assembly.step", "bracket_base.dxf", "fem_result.json", "bracket_bom.md"):
        assert (out / name).stat().st_size > 200, name
    assert r["dxf_circles"] == 2
