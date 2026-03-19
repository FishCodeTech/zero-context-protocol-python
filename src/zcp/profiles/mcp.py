from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MCPProfile:
    name: str = "ZCP-MCP/1"
    description: str = "MCP interoperability profile for ZCP"
