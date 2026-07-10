from __future__ import annotations

from .v2.application import MatchingApplication, PairSnapshot
from .v2.domain import Candidate, Selection, SourceAdapter, SourceResult
from .v2.engine import MatchingEngine
from .v2.pairing import PairingPlan, build_pairing_plan
from .v2.registry import SourceRegistry
from .v2.ttml_plan import ChangePlan, TtmlPlanner, TtmlWriter


def main(argv: list[str] | None = None) -> int:
    from .cli import main as cli_main

    return cli_main(argv)


__all__ = [
    "Candidate",
    "ChangePlan",
    "MatchingApplication",
    "MatchingEngine",
    "PairSnapshot",
    "PairingPlan",
    "Selection",
    "SourceAdapter",
    "SourceRegistry",
    "SourceResult",
    "TtmlPlanner",
    "TtmlWriter",
    "build_pairing_plan",
    "main",
]
