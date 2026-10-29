"""Re-export shim — the canonical location is now services/api/app/modules/mcp_client.py.

Kept so skill-local smoke-test scripts (test_diffcsp.py, etc.) continue to
`from mcp_client import MCPClient` when run from this directory. The durable
worker imports from services/api/app/modules/mcp_client.py instead.
"""
import sys
import pathlib

_here = pathlib.Path(__file__).resolve().parent
_modpath = _here.parents[4] / "services" / "api" / "app" / "modules"
if str(_modpath) not in sys.path:
    sys.path.insert(0, str(_modpath))

from mcp_client import MCPClient, MCPError, env_url, cif_content_from, ensure_self_on_path  # noqa: F401,E402
