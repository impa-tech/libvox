"""LibVox MCP server — the Model Context Protocol face of the library."""

from .config import Settings
from .server import create_server

__all__ = ["Settings", "create_server"]
__version__ = "0.1.0"
