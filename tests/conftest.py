import os, shutil, sys, tempfile
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FREECADCMD", __import__("shutil").which("freecadcmd") or "freecadcmd")
os.environ.setdefault("FREECAD_MCP_WORKSPACE", tempfile.mkdtemp(prefix="fcmcp_ws_"))

@pytest.fixture(scope="session")
def srv():
    import server
    return server
