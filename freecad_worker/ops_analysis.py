"""Analysis operations: mass properties, interference, clearance. (FEM lives in ops_fem.py)"""
import FreeCAD as App
import Part
from FreeCAD import Vector as V

from freecad_worker.core import op, r3, get, pick, com
from freecad_worker.materials import get_material

TOL_MM3 = 0.01


def _density(o, override, material):
    mat = material or (override or {}).get(o.Name) or o.Material
    if not mat:
        return None, None
    key, m = get_material(mat)
    return key, m["density"]


@op("mass_properties", mutates=False)
def mass_properties(doc, objects=None, material=None, density_kg_m3=None):
    rows, M, Msum, I = [], 0.0, V(0, 0, 0), App.Matrix()
    items = []
    for o in pick(doc, objects):
        key, rho = _density(o, None, material)
        if density_kg_m3 is not None:
            key, rho = "custom", float(density_kg_m3)
        s = o.Shape
        row = dict(name=o.Name, volume_mm3=round(s.Volume, 3), material=key)
        if rho is None:
            row["note"] = "no material set -> no mass (use set_properties or the material argument)"
        else:
            m = s.Volume * rho * 1e-6          # kg/m3 -> g/mm3
            c_ = com(s)
            row.update(mass_g=round(m, 4), center_of_mass=r3(c_))
            items.append((m, c_, s, rho))
        rows.append(row)
    out = dict(objects=rows)
    if items:
        M = sum(i[0] for i in items)
        cm = V(0, 0, 0)
        for m, c, _, _ in items:
            cm = cm + c * m
        cm = cm * (1.0 / M)
        T = [[0.0] * 3 for _ in range(3)]
        for m, c, s, rho in items:
            for so in s.Solids:
                mi = so.MatrixOfInertia          # unit density, about the solid's own centre of mass (mm^5)
                Ic = [[mi.A11, mi.A12, mi.A13], [mi.A21, mi.A22, mi.A23], [mi.A31, mi.A32, mi.A33]]
                ms = so.Volume * rho * 1e-6
                d = so.CenterOfMass - cm
                dd = d.dot(d)
                dv = [d.x, d.y, d.z]
                for i in range(3):
                    for j in range(3):
                        T[i][j] += Ic[i][j] * rho * 1e-6 + ms * ((dd if i == j else 0.0) - dv[i] * dv[j])
        out.update(total_mass_g=round(M, 4), center_of_mass=r3(cm),
                   inertia_tensor_g_mm2=[[round(x, 3) for x in r] for r in T],
                   note="inertia is about the combined centre of mass, axes parallel to world axes")
    return out


@op("check_interference", mutates=False)
def check_interference(doc, objects=None, tolerance_mm3=TOL_MM3):
    objs = pick(doc, objects)
    hits, checked = [], 0
    for a in range(len(objs)):
        for b in range(a + 1, len(objs)):
            sa, sb = objs[a].Shape, objs[b].Shape
            if not sa.BoundBox.intersect(sb.BoundBox):
                continue
            checked += 1
            try:
                c = sa.common(sb)
                v = c.Volume
            except Exception as ex:
                hits.append(dict(a=objs[a].Name, b=objs[b].Name, error=str(ex)))
                continue
            if v > tolerance_mm3:
                hits.append(dict(a=objs[a].Name, b=objs[b].Name, common_volume_mm3=round(v, 4), overlap_center=r3(com(c)),
                                 overlap_bbox=[round(c.BoundBox.XLength, 3), round(c.BoundBox.YLength, 3), round(c.BoundBox.ZLength, 3)]))
    return dict(objects=len(objs), pairs_tested_after_bbox_prefilter=checked, interferences=hits, clean=not hits,
                tolerance_mm3=tolerance_mm3)
