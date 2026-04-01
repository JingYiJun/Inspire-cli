"""Structured error types for Inspire MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpToolError(Exception):
    """Structured tool error exposed through MCP responses."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload
