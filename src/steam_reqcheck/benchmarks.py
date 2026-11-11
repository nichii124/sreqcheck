"""Bundled CPU/GPU benchmark tables and name matching.

Hardware names are messy ("Intel(R) Core(TM) i5-4460 CPU @ 3.20GHz",
"NVIDIA GeForce GTX 1060 6GB", "AMD Radeon RX 6600 (RADV NAVI23)").  Both the
table entries and the names we look up are reduced to the same list of
tokens, then a table entry matches when its tokens appear, in order and
contiguously, inside the looked-up name.  Letters and digits are split into
separate tokens so "i5-4460", "i5 4460" and "i54460" all become
["i", "5", "4460"].
"""

from __future__ import annotations

import csv
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

# --------------------------------------------------------------------------- #
# Tokenizing
# --------------------------------------------------------------------------- #

_TRADEMARKS = re.compile(r"\((?:r|tm|c)\)|[®™©]", re.I)
_MEMORY = re.compile(r"\b\d+(?:\.\d+)?\s*(?:gb|gib|mb|mib|tb)(?:\s*(?:of\s+)?(?:g?ddr\d\w*|vram|ram|memory))?", re.I)
_CLOCK = re.compile(r"@?\s*\b\d+(?:\.\d+)?\s*(?:ghz|mhz)\b", re.I)
_GENERATION = re.compile(r"\b(\d+)\s*(?:st|nd|rd|th)\s*(?:gen(?:eration)?)\b", re.I)
_CORE_COUNT = re.compile(r"\b(\d+|dual|quad|hexa|six|octa|eight|twelve|sixteen)[\s-]*cores?\b", re.I)
_THREAD_COUNT = re.compile(r"\b\d+[\s-]*threads?\b", re.I)

_COMMON_NOISE = {
    "or", "and", "with", "better", "equivalent", "equiv", "higher", "above", "similar",
    "newer", "faster", "comparable", "any", "the", "a", "an", "of", "is", "required",
    "minimum", "min", "recommended", "rec", "desktop", "edition", "series", "family",
    "class", "level", "x86", "x64", "bit",
}
_GPU_NOISE = _COMMON_NOISE | {
    "nvidia", "geforce", "amd", "ati", "radeon", "intel", "graphics", "gpu", "card", "video",
    "dedicated", "compatible", "directx", "dx", "vram", "memory", "ram", "opengl",
    "vulkan", "shader", "model", "support", "supported", "lhr", "lite", "hash", "rate",
    "design", "gfx", "discrete", "integrated", "chipset",
}
_CPU_NOISE = _COMMON_NOISE | {
    "intel", "amd", "core", "processor", "processors", "cpu", "radeon", "graphics",
    "apu", "boxed", "tray", "compute", "cores", "with", "gfx",
}
# Words that mean "laptop part" are folded into one token.
_LAPTOP_WORDS = {"mobile", "laptop", "notebook", "maxq"}


def _clean(text: str) -> str:
    # Strip ™/® first: NFKC would turn "Arc™" into "ArcTM".
    t = _TRADEMARKS.sub(" ", text)
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"max\s*-?\s*q", " maxq ", t, flags=re.I)
    return t


def _split(text: str) -> list[str]:
    t = re.sub(r"[^a-z0-9+]+", " ", text.lower()).replace("+", " ")
    tokens: list[str] = []
    for word in t.split():
        tokens.extend(re.findall(r"[a-z]+|\d+", word))
    return tokens


def _tokens(text: str, noise: set[str]) -> tuple[str, ...]:
    t = _clean(text)
    t = _MEMORY.sub(" ", t)
    t = _CLOCK.sub(" ", t)
    t = _GENERATION.sub(" ", t)
    t = _CORE_COUNT.sub(" ", t)
    t = _THREAD_COUNT.sub(" ", t)
    out = []
    for tok in _split(t):
        if tok in _LAPTOP_WORDS:
            tok = "laptop"
        if tok and tok not in noise:
            out.append(tok)
    return tuple(out)


def gpu_tokens(text: str) -> tuple[str, ...]:
    return _tokens(text, _GPU_NOISE)


def cpu_tokens(text: str) -> tuple[str, ...]:
    return _tokens(text, _CPU_NOISE)


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CPUEntry:
    name: str
    single: float
    multi: float
    kind: str
    suggest: bool
    tokens: tuple[str, ...]

    @property
    def index(self) -> float:
        return cpu_index(self.single, self.multi)


@dataclass(frozen=True)
class GPUEntry:
    name: str
    score: float
    vram_gb: float | None   # smallest common variant; None for integrated GPUs
    kind: str
    suggest: bool
    tokens: tuple[str, ...]


def cpu_index(single: float, multi: float) -> float:
    """One number for gaming CPU performance.

    Games care about both per-core speed and core count, but per-core speed
    matters more, so this is a weighted geometric mean (65% single-thread,
    35% all-core).
    """
    return single ** 0.65 * multi ** 0.35


def _read_csv(filename: str) -> list[dict[str, str]]:
    text = resources.files("steam_reqcheck.data").joinpath(filename).read_text("utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


@lru_cache(maxsize=1)
def cpu_table() -> tuple[CPUEntry, ...]:
    return tuple(
        CPUEntry(
            name=r["name"],
            single=float(r["single"]),
            multi=float(r["multi"]),
            kind=r["kind"],
            suggest=r.get("suggest") == "1",
            tokens=cpu_tokens(r["name"]),
        )
        for r in _read_csv("cpus.csv")
    )


@lru_cache(maxsize=1)
def gpu_table() -> tuple[GPUEntry, ...]:
    return tuple(
        GPUEntry(
            name=r["name"],
            score=float(r["score"]),
            vram_gb=float(r["vram_gb"]) if r.get("vram_gb") else None,
            kind=r["kind"],
            suggest=r.get("suggest") == "1",
            tokens=gpu_tokens(r["name"]),
        )
        for r in _read_csv("gpus.csv")
    )


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

_CPU_LAPTOP_SUFFIXES = {"u", "h", "hs", "hx", "hk", "hq", "mq", "m", "y", "p", "laptop"}
_GPU_LAPTOP_SUFFIXES = {"m", "s", "laptop"}


def _find(needle: tuple[str, ...], hay: tuple[str, ...]) -> int:
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if hay[i : i + n] == needle:
            return i
    return -1


@dataclass(frozen=True)
class Match:
    entry: CPUEntry | GPUEntry
    exact: bool            # query tokens are exactly the entry tokens
    laptop_penalty: bool   # query looks like a laptop part but matched a desktop entry


def _best_match(query: tuple[str, ...], table, laptop_suffixes: set[str]) -> Match | None:
    if not query:
        return None
    best = None
    best_key = None
    for entry in table:
        et = entry.tokens
        if not et or len(et) > len(query):
            continue
        if len(et) < 2 and et != query:
            # One-token entries ("radeon vii") must match the whole query,
            # or tiny names would match everything.
            continue
        pos = _find(et, query)
        if pos < 0:
            continue
        after = query[pos + len(et) :]
        # "R7" must not match "R7 360": the query carries a model number the
        # entry lacks.  ("RTX 3060" inside "RTX 3060 Ti" is fine: the longer
        # "RTX 3060 Ti" entry wins when it exists.)
        if after and after[0].isdigit():
            continue
        if not any(t.isdigit() for t in et) and any(t.isdigit() for t in after):
            continue
        laptop = bool(after) and after[0] in laptop_suffixes and entry.kind == "desktop"
        if "laptop" in query and entry.kind == "desktop":
            laptop = True
        key = (len(et), et == query, not laptop)
        if best_key is None or key > best_key:
            best, best_key = (entry, et == query, laptop), key
    if best is None:
        return None
    return Match(entry=best[0], exact=best[1], laptop_penalty=best[2])


def fix_cpu_tokens(q: tuple[str, ...]) -> tuple[str, ...]:
    """'AMD r5 3600' -> ryzen 5 3600."""
    out = list(q)
    for i in range(len(out) - 2):
        if out[i] == "r" and out[i + 1] in ("3", "5", "7", "9") and out[i + 2].isdigit() and len(out[i + 2]) == 4:
            out[i] = "ryzen"
    return tuple(out)


def match_cpu_tokens(q: tuple[str, ...]) -> Match | None:
    return _best_match(fix_cpu_tokens(q), cpu_table(), _CPU_LAPTOP_SUFFIXES)


def match_cpu(name: str) -> Match | None:
    return match_cpu_tokens(cpu_tokens(name))


def match_gpu(name: str, prefer_laptop: bool = False) -> Match | None:
    return match_gpu_tokens(gpu_tokens(name), prefer_laptop, hint=name)


def looks_like_model(tokens: tuple[str, ...]) -> bool:
    """True for "GTX 970" or "i5 4460", false for "DirectX 11" or "Shader Model 5.0"."""
    return any(t.isdigit() and len(t) >= 3 for t in tokens)


def match_gpu_tokens(q: tuple[str, ...], prefer_laptop: bool = False, hint: str = "") -> Match | None:
    if q and q[0].isdigit():
        direct = _best_match(q, gpu_table(), _GPU_LAPTOP_SUFFIXES)
        if direct:
            return direct
        # "Nvidia 2060 Super" / "AMD 5700 XT": the series prefix is missing.
        h = hint.lower()
        if "nvidia" in h or "geforce" in h:
            prefixes = ("rtx", "gtx", "gt")
        elif "amd" in h or "radeon" in h or "ati" in h:
            prefixes = ("rx", "hd", "r")
        else:
            prefixes = ("rtx", "gtx", "rx")
        for p in prefixes:
            m = match_gpu_tokens((p,) + q, prefer_laptop)
            if m and m.entry.tokens[0] == p:
                return Match(m.entry, False, m.laptop_penalty)
        return None
    if prefer_laptop and "laptop" not in q:
        # Older NVIDIA laptop drivers report "GeForce GTX 1060" without
        # "Mobile"; on a laptop, prefer the laptop entry if there is one.
        m = _best_match(q + ("laptop",), gpu_table(), _GPU_LAPTOP_SUFFIXES)
        if m and m.entry.kind == "laptop" and m.entry.tokens[-1] == "laptop":
            return m
    return _best_match(q, gpu_table(), _GPU_LAPTOP_SUFFIXES)


def gpu_by_name(name: str) -> GPUEntry:
    for e in gpu_table():
        if e.name == name:
            return e
    raise KeyError(name)


def cpu_by_name(name: str) -> CPUEntry:
    for e in cpu_table():
        if e.name == name:
            return e
    raise KeyError(name)


# Representative parts for vague requirements like "Intel Core i5 4th gen".
INTEL_FAMILY_GEN = {
    "3": {2: "i3-2100", 3: "i3-3220", 4: "i3-4130", 6: "i3-6100", 7: "i3-7100", 8: "i3-8100",
          9: "i3-9100", 10: "i3-10100", 12: "i3-12100", 13: "i3-13100F", 0: "i3-2100"},
    "5": {1: "i5-750", 2: "i5-2400", 3: "i5-3470", 4: "i5-4460", 6: "i5-6500", 7: "i5-7500",
          8: "i5-8400", 9: "i5-9400", 10: "i5-10400", 11: "i5-11400", 12: "i5-12400",
          13: "i5-13400", 14: "i5-14400", 0: "i5-2400"},
    "7": {1: "i7-920", 2: "i7-2600", 3: "i7-3770", 4: "i7-4770", 6: "i7-6700", 7: "i7-7700",
          8: "i7-8700", 9: "i7-9700", 10: "i7-10700", 11: "i7-11700K", 12: "i7-12700",
          13: "i7-13700K", 14: "i7-14700K", 0: "i7-2600"},
    "9": {9: "i9-9900K", 10: "i9-10900K", 11: "i9-11900K", 12: "i9-12900K", 13: "i9-13900K",
          14: "i9-14900K", 0: "i9-9900K"},
}
RYZEN_FAMILY = {"3": "Ryzen 3 1200", "5": "Ryzen 5 1600", "7": "Ryzen 7 1700", "9": "Ryzen 9 3900X"}


def cpu_family_representative(text: str) -> tuple[CPUEntry, str] | None:
    """Map "Intel Core i5", "i7 4th gen" or "Ryzen 5" to a typical part."""
    gen_m = _GENERATION.search(text)
    gen = int(gen_m.group(1)) if gen_m else 0
    toks = cpu_tokens(text)
    for i in range(len(toks) - 1):
        if toks[i] == "i" and toks[i + 1] in INTEL_FAMILY_GEN:
            fam = INTEL_FAMILY_GEN[toks[i + 1]]
            model = fam.get(gen) or fam[0]
            m = match_cpu(model)
            if m:
                label = f"Core i{toks[i + 1]}" + (f" {gen}th gen" if gen else "")
                return m.entry, label
        if toks[i] == "ryzen" and toks[i + 1] in RYZEN_FAMILY:
            m = match_cpu(RYZEN_FAMILY[toks[i + 1]])
            if m:
                return m.entry, f"Ryzen {toks[i + 1]}"
    if "duo" in toks and "2" in toks:
        return cpu_by_name("Intel Core 2 Duo E6600"), "Core 2 Duo"
    if "quad" in toks and "2" in toks:
        return cpu_by_name("Intel Core 2 Quad Q6600"), "Core 2 Quad"
    return None


_CORE_WORDS = {"dual": 2, "quad": 4, "hexa": 6, "six": 6, "octa": 8, "eight": 8, "twelve": 12, "sixteen": 16}


def generic_cpu_requirement(text: str) -> tuple[float, int | None, float | None] | None:
    """Turn "2.4 GHz dual core" into an estimated (index, cores, ghz).

    Clock speeds are not comparable across CPU generations, so this assumes
    the developer had an older (roughly 2010-era) CPU in mind.
    """
    ghz = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*ghz", text, re.I)
    if m:
        ghz = float(m.group(1))
    else:
        m = re.search(r"(\d{3,4})\s*mhz", text, re.I)
        if m:
            ghz = float(m.group(1)) / 1000
    cores = None
    m = _CORE_COUNT.search(text)
    if m:
        w = m.group(1).lower()
        cores = int(w) if w.isdigit() else _CORE_WORDS.get(w)
    elif re.search(r"\bdual\b", text, re.I):
        cores = 2
    elif re.search(r"\bquad\b", text, re.I):
        cores = 4
    if ghz is None and cores is None:
        return None
    single = 450 * (ghz if ghz else 2.5)
    multi = single * (cores if cores else 2) * 0.53
    return cpu_index(single, multi), cores, ghz
