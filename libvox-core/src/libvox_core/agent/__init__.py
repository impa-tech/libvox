"""MARC.IA's loop: her own MCP client, not a third-party agent framework.

A package inside the Core rather than a module of its own, and the reason is
the invariant the whole tenant boundary rests on: **the model returns an intent
and the Core executes it.** Splitting the loop from the component that resolves
the tenant would mean either passing credentials across one more boundary or
resolving the tenant twice.

It owns four things nothing else does — the system prompt, the tool registry,
conversation state, and the model connection. What it deliberately does not own
is the tools themselves: those are whatever `/libvox-mcp` declares, so MARC.IA
cannot search the web for want of anything to call.
"""

from __future__ import annotations

from .prompt import POLICY, build

__all__ = ["POLICY", "build"]
