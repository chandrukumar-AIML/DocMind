from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def ensure_backend_path(file_path: str) -> Path:
    """Add the backend root to sys.path for local module execution."""
    current_file = Path(file_path).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]

    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)
    return backend_root


def module_name_from_file(file_path: str) -> str:
    """Return the canonical app.* module name for a backend/app file."""
    current_file = Path(file_path).resolve()
    parts = current_file.with_suffix("").parts
    if "app" not in parts:
        return current_file.stem
    app_index = parts.index("app")
    return ".".join(parts[app_index:])


def _public_members(module: ModuleType) -> dict[str, Any]:
    exported = getattr(module, "__all__", None)
    if exported:
        return {name: getattr(module, name) for name in exported if hasattr(module, name)}
    return {name: value for name, value in vars(module).items() if not name.startswith("_")}


def run_module_smoke(module: ModuleType, file_path: str) -> bool:
    """
    Lightweight local smoke test for backend modules.

    It verifies that the module can execute, exposes introspectable objects, and
    that FastAPI routers have route definitions when present. Heavy service calls
    are intentionally skipped so this can run without Redis/Postgres/OpenAI.
    """
    ensure_backend_path(file_path)
    canonical_name = module_name_from_file(file_path)
    public = _public_members(module)
    classes = [name for name, value in public.items() if inspect.isclass(value)]
    functions = [name for name, value in public.items() if inspect.isfunction(value)]
    router = getattr(module, "router", None)

    print(f"Testing {canonical_name}")
    print("=" * 70)
    print(f"File: {Path(file_path).resolve()}")
    print(f"Public classes: {len(classes)}")
    print(f"Public functions: {len(functions)}")

    if router is not None:
        routes = getattr(router, "routes", [])
        assert len(routes) > 0, "router exists but has no registered routes"
        print(f"FastAPI router routes: {len(routes)}")

    metadata_helpers = [name for name in functions if name.startswith("get_") and name.endswith("_metadata")]
    if metadata_helpers:
        print(f"Metadata helpers: {', '.join(metadata_helpers)}")

    assert canonical_name.startswith("app.") or canonical_name == "app"
    print("Smoke test passed")
    return True


__all__ = ["ensure_backend_path", "module_name_from_file", "run_module_smoke"]
# Local smoke test entry point. Run: python -m

