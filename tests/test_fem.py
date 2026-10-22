import json, pytest
J = json.loads

def test_fem_cantilever(srv):
    """Cantilever beam 100x10x10 mm steel, 100 N tip load: analytic max stress = 6*F*L/(b*h^2) = 60 MPa, tip deflection F*L^3/(3EI) = 0.16 mm."""
    s = srv
    doc = "t_fem"
    s.new_document(doc)
    s.add_primitive(doc, "beam", "box", {"length": 100, "width": 10, "height": 10}, material="steel")
    out = s.run_fem(doc, "beam", fixed_faces="normal:-X x:min", loads=[{"faces": "normal:+X x:max", "force_n": 100, "direction": [0, 0, -1]}], mesh_size=3.0)
    r = J(out[0])
    assert r["max_displacement_mm"] == pytest.approx(0.16, rel=0.2)        # Euler-Bernoulli 0.157 (+shear, mesh)
    assert r["displacement_vector_at_max"][2] < 0                           # deflects along the load (-Z)
    assert 50 < r["max_von_mises_mpa_raw"] < 100                            # 60 MPa at the root (+ singularity overshoot)
    assert 30 < r["max_von_mises_mpa"] <= r["max_von_mises_mpa_raw"]
    assert 2.5 < r["safety_factor_yield_raw_peak"] < 5
    assert out[1].data[:4] == b"\x89PNG"
