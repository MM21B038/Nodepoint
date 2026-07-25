from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, List

TransportKind = Literal["http", "stdio", "sse"]


class MCPConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    enabled: bool
    transport: TransportKind
    url: Optional[str] = None
    headers: Dict[str, str] | None = None
    verify: bool | str | None = None
    command: Optional[str] = None
    args: List[str] | None = None
    env: Dict[str, str] | None = None
    cwd: Optional[str] = None
    include_tools: List[str] | None = None
    exclude_tools: List[str] | None = None
    tools: Dict[str, Dict[str, Any]] | None = None

    def to_json_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "enabled": self.enabled,
            "transport": self.transport,
        }
        if self.transport in ("http", "sse"):
            d["url"] = self.url
            if self.headers:
                d["headers"] = self.headers
            if self.verify is not None:
                d["verify"] = self.verify
        if self.transport == "stdio":
            d["command"] = self.command
            d["args"] = self.args or []
            if self.env:
                d["env"] = self.env
            if self.cwd:
                d["cwd"] = self.cwd
        if self.include_tools:
            d["include_tools"] = self.include_tools
        if self.exclude_tools:
            d["exclude_tools"] = self.exclude_tools
        if self.tools:
            d["tools"] = self.tools
        return d


class MCPRegistry:
    """
    Persists MCP server config under `app/registry/tools/mcp_servers.json`.
    """

    _clients: Dict[str, Any] = {}

    def __init__(self, config_path: str | Path | None = None):
        base_dir = Path(__file__).resolve().parent / "tools"
        self.config_path = Path(config_path) if config_path else (base_dir / "mcp_servers.json")

    def load_raw(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {"mcpServers": {}}
        return json.loads(self.config_path.read_text(encoding="utf-8") or "{}")

    def save_raw(self, data: Dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def has_server(self, name: str) -> bool:
        raw = self.load_raw()
        servers = raw.get("mcpServers", {}) or {}
        return isinstance(servers, dict) and name in servers

    def list_servers(self) -> List[str]:
        raw = self.load_raw()
        servers = raw.get("mcpServers", {}) or {}
        return sorted([k for k in servers.keys() if isinstance(k, str)])

    def get_server(self, name: str) -> MCPServerConfig:
        raw = self.load_raw()
        servers = raw.get("mcpServers", {}) or {}
        cfg = servers.get(name)
        if not isinstance(cfg, dict):
            raise MCPConfigError(f"Unknown MCP server: {name}")

        enabled = bool(cfg.get("enabled", True))
        transport: TransportKind = cfg.get("transport") or ("stdio" if "command" in cfg else "http")

        headers = cfg.get("headers") or {}
        if headers is not None and not isinstance(headers, dict):
            raise MCPConfigError(f"Invalid headers for {name}")

        tools = cfg.get("tools")
        if tools is not None and not isinstance(tools, dict):
            raise MCPConfigError(f"Invalid tools map for {name}")

        return MCPServerConfig(
            name=name,
            enabled=enabled,
            transport=transport,
            url=cfg.get("url"),
            headers={str(k): str(v) for k, v in (headers or {}).items()},
            verify=cfg.get("verify"),
            command=cfg.get("command"),
            args=list(cfg.get("args") or []),
            env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()} if cfg.get("env") else None,
            cwd=cfg.get("cwd"),
            include_tools=list(cfg.get("include_tools") or []) or None,
            exclude_tools=list(cfg.get("exclude_tools") or []) or None,
            tools=tools,
        )

    def is_tool_enabled(self, server: str, tool_name: str) -> bool:
        cfg = self.get_server(server)
        if not cfg.enabled:
            return False
        if cfg.tools and isinstance(cfg.tools.get(tool_name), dict):
            if cfg.tools[tool_name].get("enabled") is False:
                return False
        if cfg.include_tools and tool_name not in set(cfg.include_tools):
            return False
        if cfg.exclude_tools and tool_name in set(cfg.exclude_tools):
            return False
        return True

    def upsert_server(self, name: str, config: Dict[str, Any]) -> None:
        raw = self.load_raw()
        raw.setdefault("mcpServers", {})
        if not isinstance(raw["mcpServers"], dict):
            raw["mcpServers"] = {}
        raw["mcpServers"][name] = config
        self.save_raw(raw)
        self._clients.pop(name, None)

    def remove_server(self, name: str) -> None:
        raw = self.load_raw()
        servers = raw.get("mcpServers")
        if isinstance(servers, dict) and name in servers:
            del servers[name]
            self.save_raw(raw)
        self._clients.pop(name, None)

    def _build_transport(self, cfg: MCPServerConfig):
        try:
            from fastmcp.client.transports import StdioTransport, StreamableHttpTransport, SSETransport
        except ModuleNotFoundError as e:
            raise MCPConfigError(
                "fastmcp is not installed. Install project dependencies (e.g. `uv sync`)."
            ) from e

        if cfg.transport == "stdio":
            if not cfg.command:
                raise MCPConfigError(f"stdio server {cfg.name} missing command")
            return StdioTransport(
                command=cfg.command,
                args=cfg.args or [],
                env=cfg.env or None,
                cwd=cfg.cwd or None,
            )
        if cfg.transport == "http":
            if not cfg.url:
                raise MCPConfigError(f"http server {cfg.name} missing url")
            return StreamableHttpTransport(
                url=cfg.url,
                headers=cfg.headers or None,
                verify=True if cfg.verify is None else cfg.verify,
            )
        if cfg.transport == "sse":
            if not cfg.url:
                raise MCPConfigError(f"sse server {cfg.name} missing url")
            return SSETransport(
                url=cfg.url,
                headers=cfg.headers or None,
                verify=True if cfg.verify is None else cfg.verify,
            )
        raise MCPConfigError(f"Unknown transport: {cfg.transport}")

    def get_client(self, name: str):
        if name in self._clients:
            return self._clients[name]
        cfg = self.get_server(name)
        transport = self._build_transport(cfg)
        try:
            from fastmcp import Client
        except ModuleNotFoundError as e:
            raise MCPConfigError(
                "fastmcp is not installed. Install project dependencies (e.g. `uv sync`)."
            ) from e

        client = Client(transport)
        self._clients[name] = client
        return client
