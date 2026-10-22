# FreeCAD Engineering MCP

A Model Context Protocol server that lets an AI client drive FreeCAD (headless) as an engineering tool: model parts,
check mass and interference, run FEM, make drawings and BOMs, and look at renders. (README is being expanded; see
server.py for the authoritative tool list and docstrings.)

Setup: `uv sync`, set `FREECADCMD` to your freecadcmd binary, then `claude mcp add freecad -- uv --directory <this dir> run server.py`.
