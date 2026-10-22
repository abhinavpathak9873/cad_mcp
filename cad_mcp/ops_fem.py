"""Linear static FEM through FreeCAD's FEM workbench: Gmsh (2nd-order tets) + CalculiX."""
import os, shutil, tempfile
import numpy as np
import FreeCAD as App
import Part
from FreeCAD import Vector as V

from cad_mcp.core import op, get, resolve_faces, r3, vec
from cad_mcp.materials import get_material


def _surface_tris(fm, shape, node_vals, idx):
    tris, vals = [], []
    for face in shape.Faces:
        try:
            els = fm.getFacesByFace(face)
        except Exception:
            continue
        for e in els:
            ns = fm.getElementNodes(e)[:3]
            tris.append([[fm.Nodes[n].x, fm.Nodes[n].y, fm.Nodes[n].z] for n in ns])
            vals.append(sum(node_vals[idx[n]] for n in ns) / 3.0)
    return np.array(tris, float), np.array(vals, float)


@op("run_fem", mutates=False)
def run_fem(doc, name, material=None, fixed_faces=None, loads=None, mesh_size=4.0, plot_path=None, result_path=None,
            exclude_mm=None, plot_view="iso"):
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    from femtools import ccxtools
    src = get(doc, name)
    shape = src.Shape.copy()
    if len(shape.Solids) != 1:
        raise ValueError("run_fem needs exactly one solid (fuse parts first)")
    mkey, m = get_material(material or src.Material)
    fixed = resolve_faces(shape, fixed_faces if fixed_faces is not None else [])
    if not fixed:
        raise ValueError("fixed_faces is empty: the part must be constrained")
    if not loads:
        raise ValueError("loads is empty")
    mesh_size = float(mesh_size)
    fd = App.newDocument("fem")
    part = fd.addObject("Part::Feature", "Geom"); part.Shape = shape
    ana = ObjectsFem.makeAnalysis(fd, "Analysis")
    solver = ObjectsFem.makeSolverCalculiXCcxTools(fd, "CCX")
    solver.AnalysisType = "static"; solver.GeometricalNonlinearity = "linear"
    ana.addObject(solver)
    mat = ObjectsFem.makeMaterialSolid(fd, "Mat")
    d = mat.Material
    d.update({"Name": mkey, "YoungsModulus": f"{m['E']} MPa", "PoissonRatio": str(m["nu"]), "Density": f"{m['density']} kg/m^3"})
    mat.Material = d; ana.addObject(mat)
    fx = ObjectsFem.makeConstraintFixed(fd, "Fixed"); fx.References = [(part, f"Face{i}") for i in fixed]; ana.addObject(fx)
    constrained = set(fixed)
    summary_loads = []
    for k, L in enumerate(loads):
        ids = resolve_faces(shape, L["faces"])
        if not ids:
            raise ValueError(f"load {k}: face selector matched nothing")
        constrained |= set(ids)
        if "pressure_mpa" in L:
            c = ObjectsFem.makeConstraintPressure(fd, f"P{k}"); c.References = [(part, f"Face{i}") for i in ids]
            c.Pressure = f"{L['pressure_mpa']} MPa"; ana.addObject(c)
            summary_loads.append(dict(faces=ids, pressure_mpa=L["pressure_mpa"]))
            continue
        dirv = vec(L["direction"]); dirv.normalize()
        helper = fd.addObject("Part::Feature", f"Dir{k}")     # a line along the force direction: FreeCAD takes the edge as direction
        helper.Shape = Part.makeLine(V(0, 0, 0), dirv * 10.0)
        c = ObjectsFem.makeConstraintForce(fd, f"F{k}"); c.References = [(part, f"Face{i}") for i in ids]
        c.Force = f"{float(L['force_n'])} N"; c.Direction = (helper, ["Edge1"]); c.Reversed = False
        ana.addObject(c)
        summary_loads.append(dict(faces=ids, force_n=L["force_n"], direction=[round(x, 4) for x in (dirv.x, dirv.y, dirv.z)]))
    mesh = ObjectsFem.makeMeshGmsh(fd, "Mesh"); mesh.Shape = part
    mesh.CharacteristicLengthMax = mesh_size; mesh.CharacteristicLengthMin = mesh_size * 0.4; mesh.ElementOrder = "2nd"
    ana.addObject(mesh)
    fd.recompute()
    err = GmshTools(mesh).create_mesh()
    fd.recompute()
    fm = mesh.FemMesh
    if fm.VolumeCount == 0:
        raise RuntimeError(f"meshing failed: {err}")
    wd = tempfile.mkdtemp(prefix="fcmcp_fem_")
    fea = ccxtools.FemToolsCcx(ana, solver)
    fea.update_objects(); fea.setup_working_dir(wd); fea.setup_ccx()
    msg = fea.check_prerequisites()
    if msg:
        raise RuntimeError(f"FEM prerequisites: {msg}")
    fea.write_inp_file(); fea.ccx_run(); fea.load_results()
    res = [o for o in fd.Objects if o.isDerivedFrom("Fem::FemResultObject")]
    if not res:
        raise RuntimeError("CalculiX produced no results (check constraints: the part may be under-constrained)")
    r = res[0]
    vm = list(r.vonMises); dl = list(r.DisplacementLengths); nn = list(r.NodeNumbers); dv = list(r.DisplacementVectors)
    idx = {n: i for i, n in enumerate(nn)}
    vmax = max(vm)
    # Stress right at a fixed face or a load patch is a numerical singularity; also report the max away from those edges.
    excl = float(exclude_mm if exclude_mm is not None else mesh_size)
    edges = [e for i in constrained for e in shape.Faces[i - 1].Edges]
    best, at = None, None
    for k in np.argsort(vm)[::-1][:600]:
        p = fm.Nodes[nn[k]]
        if min(Part.Vertex(p).distToShape(e)[0] for e in edges) >= excl:
            best, at = vm[k], p
            break
    away = best if best is not None else vmax
    dmax_i = int(np.argmax(dl))
    out = dict(object=name, material=mkey, yield_mpa=m["yield_mpa"], mesh_size_mm=mesh_size,
               nodes=fm.NodeCount, tets=fm.VolumeCount, fixed_faces=fixed, loads=summary_loads,
               max_von_mises_mpa_raw=round(vmax, 3), max_von_mises_mpa=round(away, 3),
               max_von_mises_location=r3(at) if at is not None else None,
               exclusion_mm=excl, max_displacement_mm=round(max(dl), 5),
               max_displacement_at=r3(fm.Nodes[nn[dmax_i]]), displacement_vector_at_max=[round(x, 5) for x in (dv[dmax_i].x, dv[dmax_i].y, dv[dmax_i].z)])
    ym = out["yield_mpa"]
    out["safety_factor_yield"] = round(ym / away, 3)
    out["safety_factor_yield_raw_peak"] = round(ym / vmax, 3)
    out["note"] = ("max_von_mises_mpa/safety_factor_yield exclude nodes within exclusion_mm of constraint/load edges (singularities); "
                   "the *_raw values include them. Linear static, small displacement, isotropic material.")
    if plot_path:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        import render as RD
        tris, vals = _surface_tris(fm, shape, vm, idx)
        os.makedirs(os.path.dirname(plot_path), exist_ok=True)
        cap = min(vmax, max(away * 1.5, 1e-6))
        open(plot_path, "wb").write(RD.render_field_png(tris, vals, cap, plot_view, 900,
                                   f"{name} ({mkey}) von Mises, max {away:.1f} MPa (raw peak {vmax:.1f}), SF {out['safety_factor_yield']}"))
        out["plot_png"] = plot_path
    shutil.rmtree(wd, ignore_errors=True)
    return out
