"""
darkHUB Seedream 4.5
ComfyUI custom node package for Freepik Seedream v4.5 generate/edit workflows.
"""

from ._version import VERSION as __version__
from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
    "__version__",
]
