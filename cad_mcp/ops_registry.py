"""Which worker operations modify the document (pure Python so the server can import it without FreeCAD).
The server snapshots the document before these run, giving `undo`."""
MUTATING = {"new_document", "add_primitive", "extrude_polygon", "boolean", "transform", "fillet", "chamfer", "delete_object",
            "set_properties", "import_step", "run_script", "create_sketch", "pad", "pocket", "revolve", "loft", "sweep", "hole",
            "shell", "mirror", "linear_pattern", "polar_pattern"}
