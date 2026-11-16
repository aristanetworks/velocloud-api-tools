"""Convenience re-exports for WAN configuration types.

Usage:
    from pyvelo_intent.config.wan import WanLink, WanNetwork, WanData
"""

from pyvelo.client.models.configuration.wan.link import WanLink
from pyvelo.client.models.configuration.wan.network import WanNetwork
from pyvelo.client.models.configuration.wan.root import WanData

__all__ = [
    "WanData",
    "WanLink",
    "WanNetwork",
]
