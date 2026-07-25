from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml


class Server:
    base_dir: Path = Path(__file__).resolve().parent / "tools"
    bin_dir: Path = Path(__file__).resolve().parent / "bin"

    @classmethod
    def find_server_files(cls) -> List[Path]:
        """Recursively find all SERVER.md files under tools/"""
        return list(cls.base_dir.rglob("SERVER.md"))

    @classmethod
    def extract_metadata(cls, file_path: Path) -> Dict[str, Any]:
        """
        Extract YAML frontmatter metadata from a markdown file.
        Assumes metadata is between --- blocks.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if content.startswith("---"):
            try:
                _, meta, _ = content.split("---", 2)
                return yaml.safe_load(meta) or {}
            except Exception:
                return {}

        return {}

    @classmethod
    def list_servers(cls) -> List[Dict[str, Any]]:
        """Return metadata from all SERVER.md files"""
        metadata_list: List[Dict[str, Any]] = []

        for file_path in cls.find_server_files():
            metadata = cls.extract_metadata(file_path)
            metadata_list.append(metadata)

        return metadata_list

    @classmethod
    def create(
        cls, name: str, description: str, tags: List[str] | None = None
    ) -> str:
        """
        Create a new server folder with metadata.
        """
        if tags is None:
            tags = []
        server_path = cls.base_dir / name

        if server_path.exists():
            return f"Server '{name}' already exists."

        # Create directory
        server_path.mkdir(parents=True, exist_ok=True)

        # Create __init__.py
        (server_path / "__init__.py").touch()

        # Create SERVER.md with YAML metadata
        metadata = {
            "name": name,
            "description": description,
            "tags": tags,
            "type": "public",
            "active": True,
        }

        md_content = f"""---\n{yaml.dump(metadata, sort_keys=False)}---\n"""

        with open(server_path / "SERVER.md", "w", encoding="utf-8") as f:
            f.write(md_content)

        return f"Server '{name}' created successfully."


    @classmethod
    def _get_paths(cls, name: str) -> Tuple[Path, Path]:
        server_path = cls.base_dir / name
        md_file = server_path / "SERVER.md"
        return server_path, md_file

    @classmethod
    def _load_metadata(cls, md_file: Path) -> Dict[str, Any]:
        metadata = cls.extract_metadata(md_file)
        return metadata or {}

    @classmethod
    def _save_metadata(cls, md_file: Path, metadata: Dict[str, Any]) -> None:
        md_content = f"---\n{yaml.dump(metadata, sort_keys=False)}---\n"
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_content)

    @classmethod
    def _is_private(cls, metadata: Dict[str, Any]) -> bool:
        return metadata.get("type") == "private"

    @classmethod
    def update(
        cls, name: str, description: str | None = None, tags: List[str] | None = None
    ) -> str:
        server_path, md_file = cls._get_paths(name)

        if not server_path.exists() or not md_file.exists():
            return f"No server found with name '{name}'."

        metadata = cls._load_metadata(md_file)

        if cls._is_private(metadata):
            return f"Server '{name}' is private. Cannot update."

        if description is not None:
            metadata.update({
                "description": description
            })
            
        if tags is not None:
            metadata.update({
                "tags": tags
            })

        cls._save_metadata(md_file, metadata)
        return f"Server '{name}' updated successfully."

    @classmethod
    def status(cls, name: str) -> Tuple[bool, Any]:
        server_path, md_file = cls._get_paths(name)

        if not server_path.exists() or not md_file.exists():
            return (False, f"No server found with name '{name}'.")

        metadata = cls._load_metadata(md_file)

        if "active" not in metadata:
            return (False, "No `active` parameter in metadata")

        return (True, metadata["active"])

    @classmethod
    def _set_active(cls, name: str, value: bool) -> Tuple[bool, str]:
        server_path, md_file = cls._get_paths(name)

        if not server_path.exists() or not md_file.exists():
            return (False, f"No server found with name '{name}'.")

        metadata = cls._load_metadata(md_file)

        if cls._is_private(metadata):
            return (False, f"Server '{name}' is private. Cannot change active state.")

        current = metadata.get("active")

        if current == value:
            state = "active" if value else "inactive"
            return (True, f"Server already {state}.")

        metadata["name"] = name
        metadata["active"] = value

        cls._save_metadata(md_file, metadata)

        action = "activated" if value else "deactivated"
        return (True, f"Server '{name}' {action} successfully.")

    @classmethod
    def activate(cls, name: str) -> Tuple[bool, str]:
        return cls._set_active(name, True)

    @classmethod
    def deactivate(cls, name: str) -> Tuple[bool, str]:
        return cls._set_active(name, False)

    @classmethod
    def delete(cls, name: str) -> str:
        """
        Soft delete a server by moving it to bin/
        """
        source = cls.base_dir / name
        destination = cls.bin_dir / name
    
        if not source.exists():
            return f"No server found with name '{name}'."
    
        # Ensure bin directory exists
        cls.bin_dir.mkdir(parents=True, exist_ok=True)
    
        if destination.exists():
            return f"Server '{name}' already exists in bin."
    
        source.rename(destination)
    
        return f"Server '{name}' moved to bin successfully."
        
    
    @classmethod
    def recover(cls, name: str) -> str:
        """
        Recover a server from bin/ back to tools/
        """
        source = cls.bin_dir / name
        destination = cls.base_dir / name
    
        if not source.exists():
            return f"No server found with name '{name}' in bin."
    
        if destination.exists():
            return f"A server with name '{name}' already exists in tools."
    
        destination.parent.mkdir(parents=True, exist_ok=True)
    
        source.rename(destination)
    
        return f"Server '{name}' recovered successfully."
