"""Model gateway: the single seam between the factory and model providers."""

from palimpsest.factory.gateway.client import (
    GatewayError,
    ModelRequest,
    ModelResponse,
    generate,
)

__all__ = ["GatewayError", "ModelRequest", "ModelResponse", "generate"]
