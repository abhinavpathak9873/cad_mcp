"""Material data shared by the server and the worker. Pure Python, no FreeCAD import.

density: kg/m^3, E: MPa, nu: Poisson ratio, yield_mpa: MPa (typical handbook values, not certified)."""

MATERIALS = {
    "aluminium_6061": dict(density=2700, E=68900, nu=0.33, yield_mpa=276, color=(0.72, 0.74, 0.78)),
    "steel":          dict(density=7850, E=210000, nu=0.30, yield_mpa=250, color=(0.45, 0.47, 0.50)),
    "stainless_304":  dict(density=8000, E=193000, nu=0.29, yield_mpa=215, color=(0.62, 0.64, 0.66)),
    "pa12":           dict(density=1010, E=1700, nu=0.40, yield_mpa=48,  color=(0.80, 0.80, 0.76)),
    "pla":            dict(density=1240, E=3500, nu=0.36, yield_mpa=50,  color=(0.30, 0.62, 0.40)),
    "pom":            dict(density=1410, E=2900, nu=0.35, yield_mpa=65,  color=(0.93, 0.93, 0.90)),
}
ALIASES = {"aluminium": "aluminium_6061", "aluminum": "aluminium_6061", "al6061": "aluminium_6061",
           "6061": "aluminium_6061", "al": "aluminium_6061", "aluminum_6061": "aluminium_6061",
           "steel_s235": "steel", "stainless": "stainless_304", "nylon": "pa12", "delrin": "pom"}

def get_material(name):
    key = (name or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in MATERIALS:
        raise ValueError(f"unknown material {name!r}; known: {sorted(MATERIALS)}")
    return key, MATERIALS[key]
