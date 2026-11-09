"""LibVox Core — the tenant boundary, and the implementation behind the tools.

`/libvox-mcp` declares the tools; this component answers them, resolving every
call against exactly one library. Search comes from the Discovery Index and
never from an ILS; availability comes from a driver, live, with a credential
decrypted for that one call.

This is the tool-implementation slice. The agent loop and the channel gateway —
the other two things the architecture puts here — are not built yet; see the
README.
"""

from __future__ import annotations

from .app import create_app
from .config import ConfigurationError, Settings

__version__ = "0.1.0"

__all__ = ["ConfigurationError", "Settings", "create_app"]
