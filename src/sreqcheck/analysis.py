"""Comparing this PC with a game's requirements and scoring the result."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from . import benchmarks as bm
from .hardware import GPUInfo, SystemSpecs
from .online import OnlineLookup
from .protondb import ProtonRating
from .steam import (
    Game,
    RawTier,
    all_sizes_gb,
    explicit_vram_gb,
    first_size_gb,
    split_alternatives,
    ssd_requirement,
)

# A part counts as "meeting" a requirement at 95% of its score: the
# benchmark tables are approximate and developers list "equivalent" parts
# that are rarely exactly equal.
PASS_RATIO = 0.95

# (hard-blocker ratio, maximum penalty) per component.  Below the
# hard-blocker ratio the game is considered unplayable.  Between it and
# PASS_RATIO the chance drops linearly up to the maximum penalty.
PENALTY = {
    "gpu": (0.35, 0.85),
    "cpu": (0.35, 0.75),
    "ram": (0.50, 0.60),
    "vram": (0.50, 0.50),
}
SSD_ON_HDD_FACTOR = 0.85


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_LISTED = "not_listed"


@dataclass
class ResolvedPart:
    text: str          # as written in the requirement / detected
    name: str          # what it was identified as
    score: float       # CPU index or GPU score
    how: str           # how the score was obtained
    estimated: bool
    vram_gb: float | None = None   # from the GPU table, when known


@dataclass
class Check:
    status: Status
    required: str = ""            # text for the requirement cell
    detail: str = ""              # explanation
    ratio: float | None = None    # yours / required
    estimated: bool = False
    hard_blocker: bool = False


@dataclass
class Component:
    key: str
    label: str
    yours: str
    minimum: Check
    recommended: Check
    yours_estimated: bool = False


@dataclass
class Suggestion:
    component: str
    text: str


@dataclass
class Analysis:
    game: Game
    specs: SystemSpecs
    components: list[Component]
    proton: ProtonRating | None
    chance: int
    chance_reasons: list[str]
    confidence: str
    verdict: str
    verdict_detail: str
    storage_note: str
    suggestions: list[Suggestion]
    notes: list[str] = field(default_factory=list)
    has_recommended: bool = True
    proton_overhead: float = 0.0
    cpu_part: ResolvedPart | None = None
    gpu_part: ResolvedPart | None = None
    gpu_info: GPUInfo | None = None


# --------------------------------------------------------------------------- #
# Resolving part names to scores
# --------------------------------------------------------------------------- #

def _short(name: str) -> str:
    for prefix in ("NVIDIA GeForce ", "NVIDIA ", "AMD Radeon ", "AMD ", "Intel Core ", "Intel "):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


class Resolver:
    def __init__(self, online: OnlineLookup):
        self.online = online

    # ---- single names
    def cpu(self, text: str, *, allow_family: bool = True) -> ResolvedPart | None:
        m = bm.match_cpu(text)
        if m:
            entry = m.entry
            if m.laptop_penalty:
                return ResolvedPart(text, entry.name + " (laptop version, estimated)", entry.index * 0.75,
                                    "estimated from the desktop version", True)
            return ResolvedPart(text, entry.name, entry.index, "benchmark table", False)
        if allow_family:
            fam = bm.cpu_family_representative(text)
            if fam:
                entry, label = fam
                return ResolvedPart(text, f"{label} (≈ {_short(entry.name)})", entry.index,
                                    f"no model given; assuming a typical {label}", True)
        if bm.looks_like_model(bm.cpu_tokens(text)):
            r = self.online.cpu(text)
            if r:
                return ResolvedPart(text, text.strip(), r.score, f"online estimate ({r.source})", True)
        return None

    def gpu(self, text: str, *, prefer_laptop: bool = False, tokens: tuple[str, ...] | None = None,
            hint: str = "") -> ResolvedPart | None:
        q = tokens if tokens is not None else bm.gpu_tokens(text)
        m = bm.match_gpu_tokens(q, prefer_laptop, hint=f"{text} {hint}")
        if m:
            entry = m.entry
            if m.laptop_penalty:
                return ResolvedPart(text, entry.name + " (laptop version, estimated)", entry.score * 0.75,
                                    "estimated from the desktop version", True)
            return ResolvedPart(text, entry.name, entry.score, "benchmark table", False, entry.vram_gb)
        if bm.looks_like_model(q):
            r = self.online.gpu(text)
            if r:
                return ResolvedPart(text, text.strip(), r.score, f"online estimate ({r.source})", True)
        return None

    # ---- requirement lines with alternatives
    def cpu_requirement(self, text: str) -> tuple[list[ResolvedPart], list[str]]:
        parts, unknown = [], []
        prev: tuple[str, ...] = ()
        for alt in split_alternatives(text):
            toks = bm.cpu_tokens(alt)
            if not toks:
                continue
            if toks[0].isdigit() and prev and not prev[0].isdigit() and any(t.isdigit() for t in prev):
                # "i5-4460/4590": borrow the "i5" from the previous option.
                last_num = max(i for i, t in enumerate(prev) if t.isdigit())
                alt = " ".join(prev[:last_num] + toks)
            r = self.cpu(alt)
            if r:
                parts.append(r)
            elif bm.looks_like_model(toks):
                unknown.append(alt)
            prev = bm.fix_cpu_tokens(bm.cpu_tokens(alt))
        if not parts:
            fam = bm.cpu_family_representative(text)
            if fam:
                entry, label = fam
                parts.append(ResolvedPart(text, f"{label} (≈ {_short(entry.name)})", entry.index,
                                          f"no model given; assuming a typical {label}", True))
        if not parts:
            gen = bm.generic_cpu_requirement(text)
            if gen:
                index, cores, ghz = gen
                desc = " ".join(x for x in [f"{cores} cores" if cores else "", f"{ghz:g} GHz" if ghz else ""] if x)
                parts.append(ResolvedPart(text, f"generic CPU ({desc})", index,
                                          "estimated from clock speed / core count", True))
        return parts, unknown

    def gpu_requirement(self, text: str) -> tuple[list[ResolvedPart], list[str]]:
        parts, unknown = [], []
        prev: tuple[str, ...] = ()
        for alt in split_alternatives(text):
            toks = bm.gpu_tokens(alt)
            if not toks:
                continue
            if toks[0].isdigit() and prev and not prev[0].isdigit():
                # "GTX 970/980": borrow "GTX" from the previous option.
                last_num = max((i for i, t in enumerate(prev) if t.isdigit()), default=len(prev))
                toks = prev[:last_num] + toks
            r = self.gpu(alt, tokens=toks, hint=text)
            if r:
                parts.append(r)
            elif bm.looks_like_model(toks):
                unknown.append(alt)
            prev = toks
        return parts, unknown


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def fmt_gb(value: float | None) -> str:
    if value is None:
        return "?"
    if value < 1:
        return f"{value * 1024:.0f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} TB"
    return f"{value:.0f} GB" if abs(value - round(value)) < 0.05 else f"{value:.1f} GB"


def _compare_text(ratio: float, theirs: str) -> str:
    if ratio >= 1.15:
        return f"~{ratio:.1f}× as fast as {theirs}"
    if ratio >= 1.0:
        return f"a little faster than {theirs}"
    if ratio >= PASS_RATIO:
        return f"about equal to {theirs}"
    return f"~{round((1 - ratio) * 100)}% slower than {theirs}"


def _pct_short(ratio: float) -> str:
    return f"~{round((1 - ratio) * 100)}% below"


# --------------------------------------------------------------------------- #
# The comparison
# --------------------------------------------------------------------------- #

def _part_check(kind: str, yours: ResolvedPart | None, req_text: str | None, resolver: Resolver,
                overhead: float) -> Check:
    if not req_text:
        return Check(Status.NOT_LISTED, "—", "not listed")
    if kind == "cpu":
        parts, unknown = resolver.cpu_requirement(req_text)
    else:
        parts, unknown = resolver.gpu_requirement(req_text)
    if not parts:
        if kind == "gpu" and not unknown:
            # e.g. "DirectX 11 compatible, 1 GB" - no model to compare against
            return Check(Status.PASS, req_text, "no specific graphics card named; only memory is checked",
                         estimated=True)
        return Check(Status.UNKNOWN, req_text, "couldn't identify the listed parts" +
                     (f" ({', '.join(unknown)})" if unknown else ""))
    easiest = min(parts, key=lambda p: p.score)
    if yours is None:
        return Check(Status.UNKNOWN, req_text, f"couldn't identify your {kind.upper()}", estimated=True)
    need = easiest.score * (1 + overhead)
    ratio = yours.score / need
    estimated = yours.estimated or easiest.estimated
    detail = _compare_text(ratio, _short(easiest.name))
    if overhead:
        detail += f" (with +{overhead * 100:.0f}% Proton overhead)"
    if easiest.estimated:
        detail += f" [{easiest.how}]"
    if ratio >= PASS_RATIO:
        return Check(Status.PASS, req_text, detail, ratio, estimated)
    hard, _ = PENALTY[kind]
    return Check(Status.FAIL, req_text, detail, ratio, estimated, hard_blocker=ratio < hard)


def _amount_check(kind: str, have: float | None, need: float | None, req_text: str | None,
                  estimated: bool = False) -> Check:
    if need is None:
        return Check(Status.NOT_LISTED, "—", "not listed")
    if have is None:
        return Check(Status.UNKNOWN, fmt_gb(need), f"couldn't detect your {kind.upper()}")
    ratio = have / need
    if ratio >= PASS_RATIO:
        return Check(Status.PASS, fmt_gb(need), f"{fmt_gb(have)} ≥ {fmt_gb(need)}", ratio, estimated)
    hard, _ = PENALTY[kind]
    return Check(Status.FAIL, fmt_gb(need), f"{fmt_gb(have)} < {fmt_gb(need)} ({fmt_gb(need - have)} short)",
                 ratio, estimated, hard_blocker=ratio < hard)


@dataclass
class VramNeed:
    gb: float
    source: str        # shown in the report, e.g. "GTX 960 has 2 GB"
    looked_up: bool    # True if it came from the GPU table, not the store page


def _plausible_sizes(text: str) -> list[float]:
    return [s for s in all_sizes_gb(text) if 0.03 <= s <= 48]


def vram_requirement(tier: RawTier, resolver: Resolver) -> VramNeed | None:
    """How much video memory a tier asks for.

    1. An explicit amount on the store page ("VRAM 6 GB", "4GB+ of VRAM").
    2. Otherwise each listed card: the amount written next to it
       ("GTX 1060 6GB"), or else that card's memory from the GPU table.
       As with GPU speed, meeting the easiest listed card is enough, so the
       smallest amount wins.
    """
    if tier.empty:
        return None
    gfx = tier.fields.get("graphics", "")
    explicit = explicit_vram_gb(tier.fields.get("notes", "")) or explicit_vram_gb(gfx)
    if explicit:
        return VramNeed(explicit, "listed on the store page", False)
    if not gfx:
        return None
    parts, _ = resolver.gpu_requirement(gfx)
    candidates: list[tuple[float, bool, ResolvedPart]] = []   # (gb, looked_up, part)
    for p in parts:
        sizes = _plausible_sizes(p.text)
        if sizes:
            candidates.append((min(sizes), False, p))
        elif p.vram_gb:
            candidates.append((p.vram_gb, True, p))
    if not any(not looked_up for _, looked_up, _ in candidates):
        # A size that isn't attached to a recognised card, e.g.
        # "Video card must be 1 GB or more" or "GTX 970, 4 GB".
        sizes = _plausible_sizes(gfx)
        if sizes:
            return VramNeed(min(sizes), "listed on the store page", False)
    if not candidates:
        return None
    gb, looked_up, part = min(candidates, key=lambda c: (c[0], c[1]))
    if not looked_up:
        return VramNeed(gb, "listed on the store page", False)
    source = f"{_short(part.name)} has {fmt_gb(gb)}"
    if len(candidates) > 1:
        source += " (the lowest of the listed cards)"
    return VramNeed(gb, source, True)


def _vram_check(have: float | None, need: VramNeed | None, estimated: bool) -> Check:
    check = _amount_check("vram", have, need.gb if need else None, None, estimated)
    if need:
        check.detail += f" · {need.source}"
    return check


def resolve_own_cpu(specs: SystemSpecs, resolver: Resolver, override: str | None) -> ResolvedPart | None:
    name = override or specs.cpu_name
    r = resolver.cpu(name, allow_family=bool(override))
    if r and override:
        r.how += " (from --cpu)"
    return r


def choose_gpu(specs: SystemSpecs, resolver: Resolver, override: str | None
               ) -> tuple[ResolvedPart | None, GPUInfo | None, list[tuple[GPUInfo, ResolvedPart | None]]]:
    """Pick the strongest GPU (laptops often have an integrated + a dedicated one)."""
    if override:
        r = resolver.gpu(override)
        info = GPUInfo(name=override, vendor="other", integrated=False, source="--gpu")
        return r, info, [(info, r)]
    scored = []
    for g in specs.gpus:
        r = resolver.gpu(g.name, prefer_laptop=bool(specs.is_laptop))
        if r and g.name_ambiguous:
            r.estimated = True
            r.how += " (exact model unclear from lspci)"
        scored.append((g, r))
    if not scored:
        return None, None, []

    def key(item):
        g, r = item
        return r.score if r else (1 if g.integrated is False else 0)

    g, r = max(scored, key=key)
    return r, g, scored


def analyze(game: Game, minimum: RawTier, recommended: RawTier, specs: SystemSpecs,
            proton: ProtonRating | None, online: OnlineLookup, *, proton_overhead: float = 0.0,
            cpu_override: str | None = None, gpu_override: str | None = None,
            ram_override: float | None = None, vram_override: float | None = None) -> Analysis:
    resolver = Resolver(online)
    notes: list[str] = []
    tiers = {"minimum": minimum, "recommended": recommended}
    has_rec = not recommended.empty

    # ---------------- your parts
    cpu = resolve_own_cpu(specs, resolver, cpu_override)
    gpu, gpu_info, all_gpus = choose_gpu(specs, resolver, gpu_override)
    ram_have = ram_override if ram_override is not None else specs.ram_installed_gb
    vram_have = vram_override if vram_override is not None else (gpu_info.vram_gb if gpu_info else None)
    vram_shared = False
    vram_typical = False
    if vram_have is None and gpu_info is not None and gpu_info.integrated and specs.ram_usable_gb:
        vram_have = specs.ram_usable_gb / 2   # iGPUs can borrow roughly half of system RAM
        vram_shared = True
    elif vram_have is None and gpu and gpu.vram_gb:
        vram_have = gpu.vram_gb                # couldn't detect it: use the card's usual amount
        vram_typical = True
    vram_needs = {name: vram_requirement(tier, resolver) for name, tier in tiers.items()}

    components: list[Component] = []

    # ---------------- OS / 64-bit
    def os_check(tier: RawTier) -> Check:
        if tier.empty:
            return Check(Status.NOT_LISTED, "—")
        text = tier.fields.get("os", "") or ("64-bit" if tier.requires_64bit else "")
        if not specs.is_64bit:
            return Check(Status.FAIL, text or "64-bit", "Proton needs a 64-bit x86 (x86-64) system",
                         hard_blocker=True)
        return Check(Status.PASS, text or "—", "runs through Proton")

    components.append(Component("os", "OS", f"Linux {specs.arch} ({'64-bit' if specs.is_64bit else '32-bit'})",
                                os_check(minimum), os_check(recommended)))

    # ---------------- CPU
    cpu_yours = specs.cpu_name if not cpu_override else cpu_override
    extra = []
    if specs.cpu_cores and not cpu_override:
        extra.append(f"{specs.cpu_cores}C/{specs.cpu_threads}T")
    if cpu:
        cpu_yours_disp = _short(cpu.name) + (f" ({', '.join(extra)})" if extra else "")
    else:
        cpu_yours_disp = cpu_yours + " (not in benchmark data)"
    components.append(Component(
        "cpu", "Processor", cpu_yours_disp,
        _part_check("cpu", cpu, minimum.fields.get("processor"), resolver, proton_overhead),
        _part_check("cpu", cpu, recommended.fields.get("processor"), resolver, proton_overhead),
        yours_estimated=bool(cpu and cpu.estimated),
    ))

    # ---------------- GPU
    if gpu_info:
        gpu_disp = _short(gpu.name) if gpu else gpu_info.name + " (not in benchmark data)"
        if len(all_gpus) > 1 and not gpu_override:
            others = [g.name for g, _ in all_gpus if g is not gpu_info]
            notes.append(f"Several GPUs found; judging with the strongest ({gpu_info.name}). "
                         f"Also present: {', '.join(others)}.")
    else:
        gpu_disp = "No GPU detected"
    components.append(Component(
        "gpu", "Graphics", gpu_disp,
        _part_check("gpu", gpu, minimum.fields.get("graphics"), resolver, proton_overhead),
        _part_check("gpu", gpu, recommended.fields.get("graphics"), resolver, proton_overhead),
        yours_estimated=bool(gpu and gpu.estimated),
    ))

    # ---------------- VRAM
    vram_disp = (f"{fmt_gb(vram_have)} shared with system RAM" if vram_shared
                 else f"{fmt_gb(vram_have)} (usual for this card)" if vram_typical
                 else fmt_gb(vram_have) if vram_have else "unknown")
    vram_est = vram_shared or vram_typical
    components.append(Component(
        "vram", "Video memory", vram_disp,
        _vram_check(vram_have, vram_needs["minimum"], vram_est),
        _vram_check(vram_have, vram_needs["recommended"], vram_est),
        yours_estimated=vram_est,
    ))

    # ---------------- RAM
    if ram_override is not None:
        ram_disp = f"{fmt_gb(ram_have)} (from --ram)"
    elif specs.ram_usable_gb:
        ram_disp = f"{fmt_gb(ram_have)} ({specs.ram_usable_gb:.1f} GB usable)"
    else:
        ram_disp = "unknown"
    components.append(Component(
        "ram", "Memory", ram_disp,
        _amount_check("ram", ram_have, first_size_gb(minimum.fields.get("memory", "")), None),
        _amount_check("ram", ram_have, first_size_gb(recommended.fields.get("memory", "")), None),
    ))

    # ---------------- Storage (reported, but not part of the chance)
    installed_in = [lib for lib in specs.libraries if game.app_id in lib.app_ids]
    best_lib = max(specs.libraries, key=lambda l: l.free_gb, default=None)

    def storage_check(tier: RawTier) -> Check:
        need = first_size_gb(tier.fields.get("storage", ""))
        ssd = ssd_requirement(tier)
        req = (fmt_gb(need) if need else "—") + (" SSD" if ssd == "required" else "")
        if need is None and not ssd:
            return Check(Status.NOT_LISTED, "—", "not listed")
        if installed_in:
            lib = installed_in[0]
            if ssd == "required" and lib.ssd is False:
                return Check(Status.FAIL, req, f"installed on {lib.path}, which is a hard drive (SSD required)")
            return Check(Status.PASS, req, f"already installed in {lib.path}")
        if not specs.libraries:
            return Check(Status.UNKNOWN, req, "no Steam library found")
        fits = [l for l in specs.libraries if need is None or l.free_gb >= need]
        if not fits:
            short = need - best_lib.free_gb
            return Check(Status.FAIL, req, f"most free space is {fmt_gb(best_lib.free_gb)} on {best_lib.path} "
                                           f"({fmt_gb(short)} short)", best_lib.free_gb / need)
        if ssd == "required":
            ssd_fits = [l for l in fits if l.ssd]
            if not ssd_fits:
                if any(l.ssd is None for l in fits):
                    return Check(Status.UNKNOWN, req, "enough space, but couldn't tell if the drive is an SSD")
                return Check(Status.FAIL, req, "enough space, but only on a hard drive (game requires an SSD)")
            fits = ssd_fits
        lib = max(fits, key=lambda l: (bool(l.ssd), l.free_gb))
        return Check(Status.PASS, req, f"{fmt_gb(lib.free_gb)} free on {lib.path}" + (" (SSD)" if lib.ssd else ""))

    if best_lib:
        storage_disp = f"{fmt_gb(best_lib.free_gb)} free on {best_lib.path}" + (
            " (SSD)" if best_lib.ssd else " (HDD)" if best_lib.ssd is False else "")
    else:
        storage_disp = "no Steam library found"
    st_min, st_rec = storage_check(minimum), storage_check(recommended)
    components.append(Component("storage", "Storage", storage_disp, st_min, st_rec))

    # ---------------- the chance it runs
    reasons: list[str] = []
    chance = 1.0
    hard = False
    unknowns = []
    estimates = False
    for comp in components:
        c = comp.minimum
        if comp.key == "storage":
            continue
        if c.status is Status.UNKNOWN and comp.key in ("cpu", "gpu", "ram"):
            unknowns.append(comp.label)
        if c.estimated or comp.yours_estimated:
            estimates = True
        if c.status is not Status.FAIL:
            continue
        if c.hard_blocker:
            hard = True
            reasons.append(f"{comp.label}: {c.detail} — too far below the minimum to run")
            continue
        if comp.key in PENALTY and c.ratio is not None:
            t, w = PENALTY[comp.key]
            d = min(1.0, max(0.0, (1 - c.ratio) / (1 - t)))
            factor = 1 - w * d
            chance *= factor
            reasons.append(f"{comp.label} is {_pct_short(c.ratio)} the minimum: {c.detail} "
                           f"(−{round((1 - factor) * 100)}%)")
    if st_min.status is Status.FAIL and "SSD" in st_min.detail and "space" in st_min.detail:
        chance *= SSD_ON_HDD_FACTOR
        reasons.append("SSD required but only a hard drive is available: expect long loads and stutter (−15%)")
    if proton is not None and not proton.error:
        if proton.tier == "borked" and not game.native_linux:
            hard = True
            reasons.append("ProtonDB rates this game Borked: it doesn't run under Proton right now")
        elif proton.tier == "borked":
            reasons.append("ProtonDB rates the Windows version Borked, but there is a native Linux version")
        else:
            if not game.native_linux:
                chance *= proton.factor
                cut = f"(−{round((1 - proton.factor) * 100)}%)"
                if proton.factor < 1 and proton.tier:
                    reasons.append(f"ProtonDB: {proton.tier.capitalize()}, {proton.meaning} {cut}")
                elif proton.factor < 1:
                    reasons.append(f"ProtonDB has no reports yet, so Proton compatibility is unknown {cut}")
    if hard:
        chance = 0.0
    pct = int(5 * round(chance * 100 / 5))
    if not hard:
        pct = max(5, pct)
    if not reasons and pct == 100:
        reasons.append("meets every minimum requirement")

    if unknowns:
        confidence = "low"
        notes.append(f"Couldn't verify: {', '.join(unknowns)}. The chance assumes these are fine.")
    elif estimates:
        confidence = "medium"
    else:
        confidence = "high"

    # ---------------- verdict
    core = [c for c in components if c.key in ("os", "cpu", "gpu", "vram", "ram")]
    min_fail = any(c.minimum.status is Status.FAIL for c in core)
    rec_fail = any(c.recommended.status is Status.FAIL for c in core)
    rec_unknown = any(c.recommended.status is Status.UNKNOWN for c in core)
    if hard:
        verdict, detail = "Won't run", "At least one requirement is far out of reach."
    elif min_fail:
        verdict, detail = "Below minimum", "It may still start, but expect low frame rates or problems."
    elif not has_rec:
        verdict, detail = "Meets minimum", "No recommended specs are listed for this game."
    elif rec_fail:
        verdict, detail = "Meets minimum", "Expect to play on low to medium settings."
    else:
        verdict, detail = "Meets recommended", "Expect medium to high settings."
    if unknowns or (rec_unknown and verdict == "Meets recommended"):
        detail += " Some parts couldn't be verified."

    storage_note = ""
    if installed_in:
        storage_note = f"Already installed in {installed_in[0].path}."
    elif st_min.status is Status.FAIL:
        storage_note = f"Not enough room to install: {st_min.detail}."
    elif st_min.status is Status.PASS:
        storage_note = f"Enough room to install ({st_min.detail})."

    if game.native_linux:
        notes.append("This game also has a native Linux version; these results use the Windows requirements.")
    if proton_overhead:
        notes.append(f"CPU/GPU requirements were raised by {proton_overhead * 100:.0f}% to allow for Proton overhead.")
    notes.extend(specs.notes)
    if online.errors:
        notes.append("Online lookup problems: " + "; ".join(sorted(set(online.errors))))

    analysis = Analysis(
        game=game, specs=specs, components=components, proton=proton, chance=pct,
        chance_reasons=reasons, confidence=confidence, verdict=verdict, verdict_detail=detail,
        storage_note=storage_note, suggestions=[], notes=notes, has_recommended=has_rec,
        proton_overhead=proton_overhead, cpu_part=cpu, gpu_part=gpu, gpu_info=gpu_info,
    )
    analysis.suggestions = suggest_upgrades(analysis, resolver, tiers, vram_needs)
    return analysis


# --------------------------------------------------------------------------- #
# Upgrade suggestions
# --------------------------------------------------------------------------- #

def _cheapest(table, need: float, score_of, vram: float | None = None) -> str | None:
    """Weakest suggestible part that still meets `need` (as a stand-in for cheapest)."""
    candidates = sorted((e for e in table if e.suggest and score_of(e) >= need
                         and (vram is None or (e.vram_gb or 0) >= vram * PASS_RATIO)), key=score_of)
    return _short(candidates[0].name) if candidates else None


def suggest_upgrades(a: Analysis, resolver: Resolver, tiers: dict[str, RawTier],
                     vram_needs: dict[str, VramNeed | None]) -> list[Suggestion]:
    out: list[Suggestion] = []
    laptop = bool(a.specs.is_laptop)
    comps = {c.key: c for c in a.components}

    def target(comp: Component) -> tuple[str, Check] | None:
        if comp.minimum.status is Status.FAIL:
            return "minimum", comp.minimum
        if comp.recommended.status is Status.FAIL:
            return "recommended", comp.recommended
        return None

    for key in ("gpu", "cpu"):
        comp = comps[key]
        t = target(comp)
        if not t or t[1].ratio is None:
            continue
        tier_name, check = t
        mine = a.gpu_part if key == "gpu" else a.cpu_part
        if not mine:
            continue
        need = mine.score / check.ratio
        if key == "gpu":
            vram_need = vram_needs.get(tier_name)
            pick = _cheapest(bm.gpu_table(), need * 1.05, lambda e: e.score, vram_need.gb if vram_need else None)
            what = "graphics card"
        else:
            pick = _cheapest(bm.cpu_table(), need * 1.05, lambda e: e.index)
            what = "processor"
        gain = round((1 / check.ratio - 1) * 100)
        text = f"To reach the {tier_name} spec you need a {what} about {gain}% faster than yours"
        if pick:
            text += f", e.g. {pick} or better"
        text += "."
        if laptop:
            text += f" Laptop {what}s usually can't be replaced."
        elif key == "cpu":
            text += " A new CPU may also need a new motherboard."
        out.append(Suggestion(comp.label, text))

    comp = comps["vram"]
    t = target(comp)
    if t:
        need = vram_needs[t[0]].gb
        gpu_tip = next((s for s in out if s.component == "Graphics"), None)
        if gpu_tip:
            gpu_tip.text += f" Pick one with at least {fmt_gb(need)} of video memory."
        else:
            out.append(Suggestion(comp.label, f"The {t[0]} spec asks for {fmt_gb(need)} of video memory. "
                                              "That's part of the graphics card, so it means a GPU upgrade."))

    comp = comps["ram"]
    t = target(comp)
    if t:
        need = first_size_gb(tiers[t[0]].fields.get("memory", ""))
        size = next((s for s in (8, 16, 24, 32, 48, 64, 96, 128) if s >= (need or 0)), need)
        text = f"Upgrade RAM to {fmt_gb(size)} to reach the {t[0]} spec."
        text += (" Many laptops have soldered RAM; check whether yours has a free slot."
                 if laptop else " Check your motherboard's free slots and maximum supported memory.")
        out.append(Suggestion(comp.label, text))

    comp = comps["storage"]
    if comp.minimum.status is Status.FAIL:
        if "short" in comp.minimum.detail:
            out.append(Suggestion(comp.label, "Free up space on a Steam library drive, or add a library "
                                              "on another drive (Steam → Settings → Storage)."))
        elif "SSD" in comp.minimum.detail or "hard drive" in comp.minimum.detail:
            out.append(Suggestion(comp.label, "Install the game to a Steam library on an SSD."))

    if a.proton and a.proton.tier in ("borked", "bronze"):
        out.append(Suggestion("Proton", f"Check {a.proton.url} for reported workarounds and Proton versions "
                                        "that work better (e.g. Proton Experimental or GE-Proton)."))
    return out
