"""Route package metadata.

The FastAPI application mounts route modules explicitly in app.main. This
package intentionally avoids importing route modules during package import so
startup does not trip over circular imports.
"""

from typing import Any, Final

from fastapi import APIRouter

__version__ = "1.0.0"
__description__ = "DocuMind API Route Package"
__api_prefix__ = "/v1"

_API_COMPONENTS: Final = ["app.api.routes"]

# ADDED: Compatibility aggregate for code that imports app.api.routes.api_router.
api_router = APIRouter()


def get_routes_metadata() -> dict[str, Any]:
    """Return route package metadata for monitoring."""
    return {
        "version": __version__,
        "description": __description__,
        "api_prefix": __api_prefix__,
        "components": _API_COMPONENTS,
    }


__all__ = [
    "api_router",
    "get_routes_metadata",
    "__version__",
]
