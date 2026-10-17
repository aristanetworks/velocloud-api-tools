"""Generic VCO API response schema."""

from pydantic import ConfigDict

from pyvelo.core import VcoModel


class GenericResult(VcoModel):
    """Generic API response for arbitrary VCO endpoints."""

    model_config = ConfigDict(extra="allow")


class GenericRequest(VcoModel):
    """Generic API request body for arbitrary VCO endpoints.

    Accepts any extra fields so callers can pass through arbitrary JSON-RPC
    parameters without a typed schema. Use the typed mixins where possible;
    this exists for unsupported endpoints.
    """

    model_config = ConfigDict(extra="allow")
