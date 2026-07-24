"""
Compliance data collectors — one module per source.
"""

from .brave_search import BraveSearchCollector
from .egov import EGovCollector
from .caa import CAACollector
from .nite import NITECollector
from .safety_korea import SafetyKoreaCollector
from .gdelt import GDELTCollector

ALL_COLLECTORS = [
    EGovCollector,
    CAACollector,
    NITECollector,
    SafetyKoreaCollector,
    BraveSearchCollector,
    GDELTCollector,
]

__all__ = [
    "BraveSearchCollector",
    "EGovCollector",
    "CAACollector",
    "NITECollector",
    "SafetyKoreaCollector",
    "GDELTCollector",
    "ALL_COLLECTORS",
]
