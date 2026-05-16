from __future__ import annotations
from typing import Any, Callable, Dict, Optional, Literal, Union
import concurrent.futures
import inspect
import asyncio
import ast
import traceback
from pathlib import Path
from types import SimpleNamespace
from pydantic import create_model
from nodepoint.agent.schema import tool_args_as_dict
import pkgutil
import importlib
import logging
from .server import Server
from .mcp import MCPRegistry


class Tool:
    registry: Dict[str, Dict[str, Any]] = {}
    _mcp = MCPRegistry()
    _local_tools_loaded: bool = False
    _log = logging.getLogger("app.registry.Tool")

    @staticmethod
    def _resolve_mcp_server_name(server: Union[str, SimpleNamespace, Any]) -> str:
        if isinstance(server, str):
            s = server.strip()
            if not s:
                raise ValueError("MCP server name must be non-empty")
            return s
        for attr in ("server", "name", "namespace"):
            if hasattr(server, attr):
                v = getattr(server, attr)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        raise TypeError(
            "server must be a str or an object with a string attribute "
            "'server', 'name', or 'namespace' (e.g. types.SimpleNamespace(server='MyServer'))."
        )

    @classmethod
    def _flat_tool_server_active(cls, server: str) -> bool | None:
        """
        New layout: one file `tools/<Server>.py` with YAML-like lines in the module docstring
        (`active: true|false`). Returns None if this server is not represented as a flat module file.
        """
        tools_dir = Path(__file__).resolve().parent / "tools"
        path = tools_dir / f"{server}.py"
        if not path.is_file():
            return None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            return None
        doc = ast.get_docstring(tree)
        if not doc:
            return True
        for raw in doc.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            if key.strip().lower() != "active":
                continue
            token = val.strip().lower()
            if token in ("true", "1", "yes"):
                return True
            if token in ("false", "0", "no"):
                return False
            return True
        return True

    @classmethod
    def _is_local_server_active(cls, server: str) -> bool:
        try:
            ok, value = Server.status(name=server)
        except Exception:
            ok, value = False, None
        if ok:
            return bool(value)
        parsed = cls._flat_tool_server_active(server)
        if parsed is not None:
            return parsed
        return True

    @classmethod
    def _is_mcp_tool_enabled(cls, server: str, tool_name: str) -> bool:
        try:
            cfg = cls._mcp.get_server(server)
        except Exception:
            return False
        if not getattr(cfg, "enabled", True):
            return False
        return cls._mcp.is_tool_enabled(server, tool_name)

    @classmethod
    def _ensure_local_tools_loaded(cls, force: bool = False) -> None:
        if cls._local_tools_loaded and not force:
            return

        base_package = __name__.rsplit(".", 1)[0]
        tools_package = f"{base_package}.tools"

        try:
            package = importlib.import_module(tools_package)
        except ModuleNotFoundError:
            cls._local_tools_loaded = True
            return

        failures: list[str] = []
        for _, mod_name, _ in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            if ".ipynb_checkpoints" in mod_name:
                continue
            leaf = mod_name.rsplit(".", 1)[-1]
            if leaf.startswith("_"):
                continue
            try:
                importlib.import_module(mod_name)
            except Exception:
                failures.append(mod_name)

        cls._local_tools_loaded = True
        if failures:
            cls._log.warning("Some tool modules failed to import: %s", ", ".join(failures))

    @classmethod
    def _unregister_mcp_tools_for_servers(cls, servers: set[str] | None) -> None:
        """Remove MCP tool entries from registry (before re-discovery)."""
        to_remove = [
            k
            for k, v in cls.registry.items()
            if v.get("kind") == "mcp" and (servers is None or v.get("server") in servers)
        ]
        for k in to_remove:
            del cls.registry[k]

    @classmethod
    async def discover_mcp_tools_async(cls, include_servers: set[str] | None = None) -> dict[str, Any]:
        """
        List tools from configured MCP servers and register as `{server}.{tool}` (same as local tools).
        """
        cls._ensure_local_tools_loaded()
        cls._unregister_mcp_tools_for_servers(include_servers)

        summary: dict[str, Any] = {
            "ok": True,
            "servers_ok": [],
            "servers_failed": [],
            "tools_registered": 0,
        }

        servers = cls._mcp.list_servers()
        if include_servers:
            servers = [s for s in servers if s in include_servers]

        for server in servers:
            try:
                cfg = cls._mcp.get_server(server)
            except Exception:
                summary["ok"] = False
                summary["servers_failed"].append({
                    "server": server,
                    "stage": "get_server",
                    "error": "Invalid MCP server config",
                })
                continue
            if not cfg.enabled:
                continue

            try:
                client = cls._mcp.get_client(server)
                async with client:
                    tools = await client.list_tools()
            except Exception as e:
                summary["ok"] = False
                summary["servers_failed"].append({
                    "server": server,
                    "stage": "list_tools",
                    "error": str(e),
                    "traceback": traceback.format_exc(limit=5),
                })
                continue

            for t in tools or []:
                tool_name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
                if not isinstance(tool_name, str) or not tool_name:
                    continue
                if not cls._mcp.is_tool_enabled(server, tool_name):
                    continue

                desc = getattr(t, "description", None) or (t.get("description") if isinstance(t, dict) else "") or ""
                schema = (
                    getattr(t, "inputSchema", None)
                    or getattr(t, "input_schema", None)
                    or (t.get("inputSchema") if isinstance(t, dict) else None)
                    or (t.get("input_schema") if isinstance(t, dict) else None)
                    or {"type": "object", "properties": {}}
                )

                full_name = f"{server}.{tool_name}"
                if full_name in cls.registry and cls.registry[full_name].get("kind") == "local":
                    cls._log.warning("Skipping MCP tool %r: name collides with a local tool.", full_name)
                    continue

                cls.registry[full_name] = {
                    "kind": "mcp",
                    "server": server,
                    "name": tool_name,
                    "description": desc,
                    "mcp_tool_name": tool_name,
                    "input_schema": schema,
                }
                summary["tools_registered"] += 1

            summary["servers_ok"].append(server)

        return summary

    @classmethod
    def register_mcp(
        cls,
        server: Union[str, SimpleNamespace, Any],
        transport: Literal["http", "stdio", "sse"],
        *,
        replace: bool = False,
        discover: bool = True,
        enabled: bool = True,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        verify: bool | str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        include_tools: list[str] | None = None,
        exclude_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Register an MCP server and persist to `app/registry/tools/mcp_servers.json`.

        Tools appear as `{server_name}.{tool_name}` — same keys as local tools for
        list_tools, schemas, invoke, invoke_async.

        `server` may be a string or `types.SimpleNamespace(server='...')` / `.name=`.
        """
        name = cls._resolve_mcp_server_name(server)
        transport = str(transport).strip().lower()
        if transport not in ("http", "stdio", "sse"):
            return {
                "ok": False,
                "error": f"invalid transport {transport!r}; use 'http', 'stdio', or 'sse'",
            }

        if cls._mcp.has_server(name) and not replace:
            return {
                "ok": False,
                "status": "exists",
                "server": name,
                "message": f"MCP server {name!r} already registered (pass replace=True to overwrite).",
            }

        cfg: dict[str, Any] = {"enabled": bool(enabled), "transport": transport}
        if transport in ("http", "sse"):
            cfg["url"] = url
            if headers:
                cfg["headers"] = headers
            if verify is not None:
                cfg["verify"] = verify
        else:
            cfg["command"] = command
            cfg["args"] = list(args or [])
            if env:
                cfg["env"] = env
            if cwd:
                cfg["cwd"] = cwd
        if include_tools:
            cfg["include_tools"] = include_tools
        if exclude_tools:
            cfg["exclude_tools"] = exclude_tools

        cls._mcp.upsert_server(name, cfg)

        result: dict[str, Any] = {
            "ok": True,
            "status": "updated" if replace else "created",
            "server": name,
            "config": cls._mcp.get_server(name).to_json_dict(),
        }

        if discover:
            result["discovery"] = cls.refresh_mcp_tools(server=name)

        return result

    @classmethod
    def refresh_mcp_tools(cls, server: str | None = None) -> dict[str, Any]:
        """
        Run MCP discovery synchronously. If an event loop is already running (e.g. Jupyter),
        discovery runs in a worker thread so `asyncio.run` is valid there.
        """
        include = {server} if server else None

        def run_discovery() -> dict[str, Any]:
            summary = asyncio.run(cls.discover_mcp_tools_async(include_servers=include))
            return {"ok": bool(summary.get("ok", True)), "summary": summary}

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return run_discovery()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(run_discovery).result()

    @classmethod
    async def refresh_tools_async(cls) -> dict[str, Any]:
        """Reload local tools and rediscover MCP tools (safe inside async/run_async)."""
        cls._ensure_local_tools_loaded(force=True)
        summary = await cls.discover_mcp_tools_async()
        return {"ok": bool(summary.get("ok", True)), "local_loaded": True, "mcp": summary}

    @classmethod
    def tool(cls, _func=None, *, name=None, description=None, server=None):
        def decorator(func: Callable):
            if server is None:
                module_parts = func.__module__.split(".")
                if "tools" in module_parts:
                    idx = module_parts.index("tools")
                    inferred_server = module_parts[idx + 1] if idx + 1 < len(module_parts) else "default"
                else:
                    inferred_server = "default"
            else:
                inferred_server = server

            base_name = name or func.__name__
            tool_name = f"{inferred_server}.{base_name}"

            tool_desc = description or (func.__doc__ or "")

            sig = inspect.signature(func)
            fields = {}

            for param_name, param in sig.parameters.items():
                annotation = (
                    param.annotation
                    if param.annotation != inspect._empty
                    else Any
                )
                default = (
                    param.default
                    if param.default != inspect._empty
                    else ...
                )
                fields[param_name] = (annotation, default)

            InputModel = create_model(f"{tool_name}_Input", **fields)

            cls.registry[tool_name] = {
                "kind": "local",
                "func": func,
                "model": InputModel,
                "description": tool_desc,
                "server": inferred_server,
                "name": base_name,
            }

            return func

        return decorator if _func is None else decorator(_func)

    @classmethod
    def invoke(cls, tool):
        cls._ensure_local_tools_loaded()
        tool_def = cls.registry[tool.name]
        kind = tool_def.get("kind", "local")

        if kind == "mcp":
            def run_mcp():
                return asyncio.run(cls.invoke_async(tool))

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return run_mcp()

            # Jupyter / nested loop: MCP client needs asyncio; run in a worker thread.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(run_mcp).result()

        args = tool_args_as_dict(getattr(tool, "args", None))
        validated = tool_def["model"](**args)
        return tool_def["func"](**validated.model_dump())

    @classmethod
    async def invoke_async(cls, tool):
        cls._ensure_local_tools_loaded()
        tool_def = cls.registry[tool.name]
        kind = tool_def.get("kind", "local")
        args = tool_args_as_dict(getattr(tool, "args", None))

        if kind == "local":
            if not cls._is_local_server_active(tool_def.get("server", "default")):
                raise RuntimeError(f"Tool server '{tool_def.get('server')}' is inactive.")
            validated = tool_def["model"](**args)
            func = tool_def["func"]
            kwargs = validated.model_dump()
            return await asyncio.to_thread(func, **kwargs)

        if kind == "mcp":
            server = tool_def["server"]
            mcp_tool_name = tool_def["mcp_tool_name"]
            if not cls._is_mcp_tool_enabled(server, mcp_tool_name):
                raise RuntimeError(f"MCP tool '{server}.{mcp_tool_name}' is disabled.")

            client = cls._mcp.get_client(server)
            async with client:
                result = await client.call_tool(mcp_tool_name, args)
                return result.data if getattr(result, "data", None) is not None else result

        raise RuntimeError(f"Unknown tool kind: {kind}")

    @classmethod
    def refresh_tools(cls) -> dict[str, Any]:
        cls._ensure_local_tools_loaded(force=True)
        mcp = cls.refresh_mcp_tools()
        return {"ok": True, "status": "refreshed", "local_loaded": True, "mcp": mcp}

    @classmethod
    def schemas(
        cls,
        include_tools: set[str] | None = None,
        exclude_tools: set[str] | None = None,
        include_servers: set[str] | None = None,
        exclude_servers: set[str] | None = None,
        include_local: bool = True,
        include_mcp: bool = True,
    ):
        cls._ensure_local_tools_loaded()
        include_tools = set(include_tools or cls.registry.keys())
        exclude_tools = set(exclude_tools or [])
        include_servers = set(include_servers or [])
        exclude_servers = set(exclude_servers or [])

        results = []

        for name, tool in cls.registry.items():
            server = tool["server"]
            kind = tool.get("kind", "local")

            if name not in include_tools or name in exclude_tools:
                continue

            if include_servers and server not in include_servers:
                continue

            if server in exclude_servers:
                continue

            if kind == "local" and not include_local:
                continue
            if kind == "mcp" and not include_mcp:
                continue
            if kind == "local" and not cls._is_local_server_active(server):
                continue
            if kind == "mcp":
                base = tool.get("name") or tool.get("mcp_tool_name") or ""
                if not cls._is_mcp_tool_enabled(server, base):
                    continue

            if kind == "mcp":
                params = tool.get("input_schema") or {"type": "object", "properties": {}}
            else:
                params = tool["model"].model_json_schema()

            results.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool["description"],
                    "parameters": params,
                },
            })

        return results

    @classmethod
    def list_tools(
        cls,
        include_tools: set[str] | None = None,
        exclude_tools: set[str] | None = None,
        include_servers: set[str] | None = None,
        exclude_servers: set[str] | None = None,
        include_local: bool = True,
        include_mcp: bool = True,
    ):
        cls._ensure_local_tools_loaded()
        include_tools = set(include_tools or cls.registry.keys())
        exclude_tools = set(exclude_tools or [])
        include_servers = set(include_servers or [])
        exclude_servers = set(exclude_servers or [])

        results: list[str] = []
        for name, tool in cls.registry.items():
            server = tool["server"]
            kind = tool.get("kind", "local")

            if name not in include_tools or name in exclude_tools:
                continue
            if include_servers and server not in include_servers:
                continue
            if server in exclude_servers:
                continue
            if kind == "local" and not include_local:
                continue
            if kind == "mcp" and not include_mcp:
                continue
            if kind == "local" and not cls._is_local_server_active(server):
                continue
            if kind == "mcp":
                base = tool.get("name") or tool.get("mcp_tool_name") or ""
                if not cls._is_mcp_tool_enabled(server, base):
                    continue

            results.append(name)

        return results

    @classmethod
    def list_servers(
        cls,
        include: set[str] | None = None,
        exclude: set[str] | None = None,
    ):
        cls._ensure_local_tools_loaded()
        servers = {t["server"] for t in cls.registry.values()}
        try:
            raw = cls._mcp.load_raw().get("mcpServers") or {}
            for mcp_name, cfg in raw.items():
                if not isinstance(mcp_name, str):
                    continue
                if isinstance(cfg, dict) and cfg.get("enabled", True):
                    servers.add(mcp_name)
        except Exception:
            pass

        if include:
            servers &= set(include)

        if exclude:
            servers -= set(exclude)

        return sorted(servers)
