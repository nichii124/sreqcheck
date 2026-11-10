"""ProtonDB compatibility summary for a Steam game."""

from __future__ import annotations

from dataclasses import dataclass

from .http import session

URL = "https://www.protondb.com/api/v1/reports/summaries/{app_id}.json"

TIER_ORDER = ["borked", "bronze", "silver", "gold", "platinum"]
TIER_FACTOR = {"platinum": 1.0, "gold": 0.95, "silver": 0.85, "bronze": 0.65, "borked": 0.0}
TIER_MEANING = {
    "platinum": "runs perfectly out of the box",
    "gold": "runs perfectly after small tweaks",
    "silver": "runs, with minor issues",
    "bronze": "runs, but often crashes or has issues",
    "borked": "doesn't run (often anti-cheat or DRM)",
}


@dataclass
class ProtonRating:
    tier: str | None            # tier used for scoring
    overall_tier: str | None
    trending_tier: str | None
    total_reports: int = 0
    confidence: str | None = None
    error: str | None = None    # network problem
    url: str = ""

    @property
    def factor(self) -> float:
        if self.tier in TIER_FACTOR:
            return TIER_FACTOR[self.tier]
        return 0.95  # no reports / unknown: a small amount of doubt

    @property
    def meaning(self) -> str:
        return TIER_MEANING.get(self.tier or "", "no reports yet")


def _worse(a: str | None, b: str | None) -> str | None:
    if a not in TIER_ORDER:
        return b if b in TIER_ORDER else a
    if b not in TIER_ORDER:
        return a
    return a if TIER_ORDER.index(a) <= TIER_ORDER.index(b) else b


def fetch(app_id: int) -> ProtonRating:
    url = f"https://www.protondb.com/app/{app_id}"
    try:
        r = session().get(URL.format(app_id=app_id), timeout=15)
    except Exception as exc:
        return ProtonRating(None, None, None, error=str(exc), url=url)
    if r.status_code == 404:
        return ProtonRating(None, None, None, url=url)
    try:
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        return ProtonRating(None, None, None, error=str(exc), url=url)
    overall = (data.get("tier") or "").lower() or None
    trending = (data.get("trendingTier") or "").lower() or None
    # Recent reports matter most (e.g. a game that added Linux-blocking
    # anti-cheat), so if the trend is worse than the overall rating, use it.
    return ProtonRating(
        tier=_worse(overall, trending),
        overall_tier=overall,
        trending_tier=trending,
        total_reports=int(data.get("total") or 0),
        confidence=data.get("confidence"),
        url=url,
    )
