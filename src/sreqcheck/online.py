"""Online fallback for parts that aren't in the bundled tables.

Uses Blender Open Data (https://opendata.blender.org), a public, openly
licensed collection of Blender benchmark results that is meant to be
downloaded and reused.  Blender measures rendering speed, not gaming, so the
scores are converted to the bundled scale with a fit computed against the
parts that appear in both datasets.  The result is a rough estimate
(typically within about +/-20%) and is always labelled as such.

Downloaded lists are cached for 30 days in ~/.cache/sreqcheck/.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from . import benchmarks
from .http import session

BLENDER_URL = "https://opendata.blender.org/benchmarks/query/"
# A recent LTS release (newer parts) and an older one (older parts).
BLENDER_VERSIONS = ("4.5.0", "3.6.0")
CACHE_MAX_AGE = 30 * 24 * 3600


def cache_dir() -> Path:
    import os

    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base) / "sreqcheck"
    d.mkdir(parents=True, exist_ok=True)
    return d


class OnlineLookup:
    """Looks up CPU/GPU scores in Blender Open Data, with local caching."""

    def __init__(self, enabled: bool = True, refresh: bool = False):
        self.enabled = enabled
        self.refresh = refresh
        self._lists: dict[tuple[str, str], list[tuple[str, float]] | None] = {}
        self._fits: dict[tuple[str, str], tuple[float, float] | None] = {}
        self.errors: list[str] = []

    # ---------------------------------------------------------------- data
    def _download(self, version: str, compute: str) -> list[tuple[str, float]] | None:
        key = (version, compute)
        if key in self._lists:
            return self._lists[key]
        path = cache_dir() / f"blender-{version}-{compute}.json"
        rows = None
        fresh = path.exists() and time.time() - path.stat().st_mtime < CACHE_MAX_AGE
        if fresh and not self.refresh:
            try:
                rows = json.loads(path.read_text())
            except (OSError, ValueError):
                rows = None
        if rows is None:
            try:
                r = session().get(
                    BLENDER_URL,
                    params={
                        "compute_type": compute,
                        "group_by": "device_name",
                        "blender_version": version,
                        "response_type": "datatables",
                    },
                    timeout=30,
                )
                r.raise_for_status()
                rows = [[row[0], float(row[1])] for row in r.json()["rows"]]
                path.write_text(json.dumps(rows))
            except Exception as exc:  # network, JSON, unexpected format
                self.errors.append(f"Blender Open Data ({version} {compute}): {exc}")
                if path.exists():  # stale cache beats nothing
                    try:
                        rows = json.loads(path.read_text())
                    except (OSError, ValueError):
                        rows = None
        if rows is not None:
            # Skip multi-socket entries such as "2X Intel Xeon ...".
            rows = [(n.strip(), s) for n, s in rows if not re.match(r"^\d+X ", n) and s > 0]
        self._lists[key] = rows
        return rows

    def _fit(self, version: str, compute: str) -> tuple[float, float] | None:
        """Fit log(our score) = a + b*log(blender score) on shared parts."""
        key = (version, compute)
        if key in self._fits:
            return self._fits[key]
        rows = self._download(version, compute) or []
        per_entry: dict[str, tuple[float, list[float]]] = {}
        for name, score in rows:
            if compute == "CPU":
                m = benchmarks.match_cpu(name)
                ours = m.entry.index if m else None
            else:
                m = benchmarks.match_gpu(name)
                ours = m.entry.score if m else None
            if m and m.exact and not m.laptop_penalty:
                per_entry.setdefault(m.entry.name, (ours, []))[1].append(score)
        pairs = [(o, statistics.median(s)) for o, s in per_entry.values()]
        result = None
        if len(pairs) >= 6:
            xs = [math.log(b) for _, b in pairs]
            ys = [math.log(o) for o, _ in pairs]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            if sxx > 0:
                b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
                result = (my - b * mx, b)
        self._fits[key] = result
        return result

    # -------------------------------------------------------------- lookup
    def _lookup(self, name: str, computes: tuple[str, ...], tokenize) -> "OnlineResult | None":
        if not self.enabled:
            return None
        query = tokenize(name)
        if not query:
            return None
        for version in BLENDER_VERSIONS:
            for compute in computes:
                rows = self._download(version, compute)
                if not rows:
                    continue
                exact = [s for n, s in rows if tokenize(n) == query]
                if not exact:
                    continue
                fit = self._fit(version, compute)
                if not fit:
                    continue
                a, b = fit
                blender_score = statistics.median(exact)
                return OnlineResult(
                    name=name,
                    score=math.exp(a + b * math.log(blender_score)),
                    source=f"Blender Open Data {version} ({compute})",
                )
        return None

    def cpu(self, name: str) -> "OnlineResult | None":
        """Returns an estimated CPU index (same scale as CPUEntry.index)."""
        return self._lookup(name, ("CPU",), benchmarks.cpu_tokens)

    def gpu(self, name: str) -> "OnlineResult | None":
        """Returns an estimated GPU score (same scale as the GPU table)."""
        toks = benchmarks.gpu_tokens(name)
        if "intel" in toks or "arc" in toks:
            order = ("ONEAPI", "HIP", "CUDA")
        elif any(t in toks for t in ("rx", "r", "hd", "vega")):
            order = ("HIP", "CUDA", "ONEAPI")
        else:
            order = ("CUDA", "HIP", "ONEAPI")
        return self._lookup(name, order, benchmarks.gpu_tokens)


@dataclass
class OnlineResult:
    name: str
    score: float
    source: str
