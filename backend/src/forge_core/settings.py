"""Workspace-scoped configuration shared by CLI and desktop Core entry points."""

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


def default_workspace_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "FORGE" / "workspaces" / "default"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FORGE" / "workspaces" / "default"
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "forge" / "workspaces" / "default"


@dataclass(frozen=True)
class Workspace:
    id: str
    root: Path

    def __post_init__(self):
        path = Path(self.root).expanduser()
        if not path.is_absolute():
            raise ValueError("Workspace root must be an absolute path")
        if not self.id.strip():
            raise ValueError("FORGE_WORKSPACE_ID must not be empty")
        object.__setattr__(self, "root", path.resolve())

    @classmethod
    def from_path(cls, root: str | Path, workspace_id: str | None = None):
        path = Path(root).expanduser()
        if not path.is_absolute():
            raise ValueError("Workspace root must be an absolute path")
        path = path.resolve()
        identity = (
            workspace_id
            if workspace_id is not None
            else hashlib.sha256(os.path.normcase(str(path)).encode()).hexdigest()[:24]
        )
        return cls(id=identity, root=path)

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        return (candidate if candidate.is_absolute() else self.root / candidate).resolve()


@dataclass(frozen=True)
class Settings:
    workspace: Workspace
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def load(cls, *, workspace_root: str | Path | None = None):
        root = (
            workspace_root
            if workspace_root is not None
            else os.environ.get("FORGE_WORKSPACE_ROOT", str(default_workspace_root()))
        )
        workspace = Workspace.from_path(root)
        env_file = workspace.resolve(os.environ.get("FORGE_ENV_FILE", ".env"))
        if "FORGE_ENV_FILE" in os.environ and not env_file.is_file():
            raise ValueError(f"FORGE_ENV_FILE does not exist: {env_file}")
        values = dotenv_values(env_file, interpolate=False) if env_file.is_file() else {}
        if "FORGE_CORE_SECRET" in values:
            raise ValueError("FORGE_CORE_SECRET must not be stored in .env")
        if "FORGE_WORKSPACE_ROOT" in values:
            raise ValueError(
                "Set FORGE_WORKSPACE_ROOT in the process environment, before loading .env"
            )

        def configured(name, default):
            value = os.environ[name] if name in os.environ else values.get(name, default)
            if value is None or not str(value).strip():
                raise ValueError(f"{name} must not be empty")
            return str(value)

        workspace = Workspace.from_path(
            workspace.root, configured("FORGE_WORKSPACE_ID", workspace.id)
        )
        try:
            port = int(configured("FORGE_PORT", "8765"))
        except ValueError as exc:
            raise ValueError("FORGE_PORT must be an integer between 1 and 65535") from exc
        if not 1 <= port <= 65535:
            raise ValueError("FORGE_PORT must be between 1 and 65535")
        host = configured("FORGE_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "::1"}:
            raise ValueError("FORGE_HOST must be a loopback IP address")
        return cls(
            workspace=workspace,
            db_path=workspace.resolve(configured("FORGE_DB_PATH", "data/forge.sqlite3")),
            host=host,
            port=port,
        )
