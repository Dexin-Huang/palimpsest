"""Model gateway: the single seam between the factory and model providers."""

from palimpsest.factory.gateway.client import generate, generate_json
from palimpsest.factory.gateway.protocol import (
    GatewayError,
    ImageContent,
    ModelRequest,
    ModelResponse,
)

__all__ = [
    "GatewayError",
    "ImageContent",
    "ModelRequest",
    "ModelResponse",
    "generate",
    "generate_json",
]
