---
name: cad-mcp
description: Use for CAD, FreeCAD, mechanical design and engineering-analysis work through the cad_mcp tools - modelling parts and assemblies, checking mass, interference and clearance, running FEM to a safety-factor target, and producing renders, drawings, BOMs and STEP/STL/DXF exports.
---

# Engineering loop with the cad_mcp tools

Work in small steps and verify every step with a number or an image. Never claim a design is good from the tool calls
alone: read the results.

## The loop

1. Start: `new_document`. Units are mm and degrees, +Z up. Decide the material early (`set_properties material=...`,
   or the `material` argument when creating); mass, FEM and the BOM need it.
2. Model: `extrude_polygon` or `create_sketch` + `pad`/`pocket`/`revolve`/`loft`/`sweep` for profiles, `add_primitive` for
   simple solids, `boolean` to combine, then `hole`, `fillet`, `chamfer`, `shell`, `mirror`, patterns. Fasteners: `add_fastener`
   (ISO-style M3-M12). Library STEP parts: `list_library`, `insert_library_part`.
3. Look at it: `render_view` (view "grid" shows iso, front, top and right at once). LOOK at the image every time and
   compare with the intent: dimensions, hole positions, orientation. `section_view` shows internal fit. `explode` in
   `render_view` for assemblies.
4. Check with numbers: `list_objects` (volume, bbox, validity), `mass_properties`, `check_interference` (must be clean unless
   overlap is intended), `check_clearance` (minimum gaps).
5. Analyse: `run_fem` to a target yield safety factor (state the target, e.g. 2.0; if it fails, thicken, add fillets or
   change material, and rerun). Look at the returned stress plot, not just the number.
6. Document: `make_drawing` (look at it), `bom`, then `export` (step, stl, obj, 3mf) or `export_dxf` for laser parts.
7. Report honestly: list every check with its number and any that failed or were not done.

## Addressing faces and edges

Indices are 1-based and change after any geometry edit, so call `list_faces` / `list_edges` again after editing. Prefer
selectors over hard-coded indices: `normal:+Z z:max` (top face), `normal:-Z z:min` (bottom), `type:cylinder radius:3.3`
(holes of that size), `area>100`, `parallel:Y length>25` (edges). Tokens are combined with AND. For FEM, `fixed_faces` can be a
list of selectors/index lists that are united. If a selector matches nothing the tool errors; list the faces and read them.

## FEM rules of thumb

- One solid per run; fuse parts first. Fix the faces that are really bolted or clamped (hole cylinders for bolted joints).
- Keep `mesh_size` coarse first (3-5 mm for hand-sized parts); refine only where the stress plot shows a concentration.
- Stress right at a fixed face or load patch is a singularity. Use `max_von_mises_mpa` (excludes it) and quote the raw peak too.
- The result is linear static: check that displacement is small relative to the part and that the safety factor is vs yield.

## Pitfalls

- Booleans: do not leave cutting tools exactly flush with a face; extend them past the surface.
- Fillet failures mean the radius is too large for the neighbouring faces; shrink it or select fewer edges.
- `undo` restores the previous saved version (no redo); `list_history` and `object_history` show what was done.
- Use `run_script` only when no dedicated tool fits; add geometry through `add_shape(doc, name, shape, material)` so the other
  tools can see it, and print results you need.
- Every tool call is a separate FreeCAD process (about 1-2 s); batch ideas into fewer, larger steps when possible.
