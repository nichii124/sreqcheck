"""Fetching a game's details from Steam and parsing its System Requirements."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .http import session

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"


class SteamError(Exception):
    pass


def parse_app_id(text: str) -> int:
    """Accepts an app ID or any Steam store / steam:// link containing one."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    for pattern in (r"/app/(\d+)", r"steam://(?:store|run|rungameid|install)/(\d+)", r"s\.team/a/(\d+)",
                    r"[?&]appids?=(\d+)"):
        m = re.search(pattern, text)
        if m:
            return int(m.group(1))
    raise SteamError(
        f"Couldn't find a Steam app ID in {text!r}. Pass a store URL like "
        "https://store.steampowered.com/app/1091500/ or just the number (1091500)."
    )


@dataclass
class Game:
    app_id: int
    name: str
    minimum_html: str
    recommended_html: str
    native_linux: bool
    is_free: bool = False
    store_url: str = ""


def fetch_game(app_id: int) -> Game:
    try:
        r = session().get(APPDETAILS_URL, params={"appids": app_id, "l": "english"}, timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        raise SteamError(f"Couldn't reach the Steam store API: {exc}") from exc
    entry = payload.get(str(app_id)) if isinstance(payload, dict) else None
    if not entry or not entry.get("success"):
        raise SteamError(
            f"Steam returned no data for app {app_id}. Check the ID; the game may also be "
            "unavailable in your region or removed from the store."
        )
    data = entry["data"]
    reqs = data.get("pc_requirements") or {}
    if not isinstance(reqs, dict):  # Steam sends [] when there are none
        reqs = {}
    return Game(
        app_id=app_id,
        name=data.get("name", f"App {app_id}"),
        minimum_html=reqs.get("minimum", "") or "",
        recommended_html=reqs.get("recommended", "") or "",
        native_linux=bool((data.get("platforms") or {}).get("linux")),
        is_free=bool(data.get("is_free")),
        store_url=f"https://store.steampowered.com/app/{app_id}/",
    )


# --------------------------------------------------------------------------- #
# Requirement text parsing
# --------------------------------------------------------------------------- #

_LABELS = {
    "os": ("os", "operating system", "system", "platform", "windows"),
    "processor": ("processor", "processors", "cpu"),
    "memory": ("memory", "ram", "system memory", "system ram"),
    "graphics": ("graphics", "graphics card", "video card", "video cards", "videocard", "video",
                 "gpu", "display", "graphic card", "graphics cards"),
    "storage": ("storage", "hard drive", "hard disk", "hard disk space", "disk space", "hdd",
                "free disk space", "hard drive space", "disk", "free space", "space", "drive",
                "storage space", "install size", "ssd"),
    "directx": ("directx", "direct x", "dx"),
    "network": ("network", "internet"),
    "sound": ("sound card", "sound", "audio"),
    "notes": ("additional notes", "notes", "additional", "other", "note", "vr support"),
}


def _label_key(label: str) -> str | None:
    lab = re.sub(r"[^a-z ]+", " ", label.lower())
    lab = re.sub(r"\s+", " ", lab).strip()
    if not lab:
        return None
    for key, names in _LABELS.items():
        if lab in names:
            return key
    for key, names in _LABELS.items():
        if any(lab.startswith(n + " ") or lab.endswith(" " + n) for n in names):
            return key
    return None


@dataclass
class RawTier:
    """The requirement lines of one column (Minimum or Recommended)."""

    fields: dict[str, str] = field(default_factory=dict)
    requires_64bit: bool = False

    @property
    def empty(self) -> bool:
        return not self.fields


def _html_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["li", "p", "div", "h1", "h2", "h3", "ul"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = soup.get_text()
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n") if ln.strip()]


_HEADER = re.compile(r"^(minimum|recommended|required|requirements|min|rec)\b[^:]*:?\s*$", re.I)


def parse_requirements_html(html: str) -> tuple[RawTier, RawTier | None]:
    """Parse one requirements HTML block.

    Returns the tier it describes and, if the block also contains a
    "Recommended:" section (some games put both in one field), that too.
    """
    first, second = RawTier(), None
    current = first
    last_key = None
    for line in _html_lines(html):
        if _HEADER.match(line):
            if line.lower().startswith("rec") and current.fields:
                second = RawTier()
                current = second
                last_key = None
            continue
        if re.search(r"64[- ]?bit", line, re.I) and re.search(r"requires|processor and operating", line, re.I):
            current.requires_64bit = True
            if ":" not in line:
                continue
        m = re.match(r"^([^:]{1,40}):\s*(.*)$", line)
        key = _label_key(m.group(1)) if m else None
        if key:
            value = m.group(2).strip()
            if key in current.fields and value:
                current.fields[key] += " " + value
            else:
                current.fields[key] = value
            last_key = key
        elif last_key:  # continuation of the previous line
            current.fields[last_key] = (current.fields[last_key] + " " + line).strip()
    for tier in (first, second):
        if tier and re.search(r"64[- ]?bit|x64", tier.fields.get("os", ""), re.I):
            tier.requires_64bit = True
    return first, second


def parse_game_requirements(game: Game) -> tuple[RawTier, RawTier]:
    minimum, extra = parse_requirements_html(game.minimum_html)
    recommended, _ = parse_requirements_html(game.recommended_html)
    if recommended.empty and extra is not None:
        recommended = extra
    return minimum, recommended


# --------------------------------------------------------------------------- #
# Value extraction helpers
# --------------------------------------------------------------------------- #

_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:[a-z]{1,4}\s+)?(tb|gb|gib|mb|mib)\b", re.I)


def _to_gb(number: str, unit: str) -> float:
    value = float(number.replace(",", "."))
    unit = unit.lower()
    if unit in ("mb", "mib"):
        # "8192 MB" means 8 GB; "500 MB" is 0.5 GB either way.
        return value / 1024
    if unit == "tb":
        return value * 1024
    return value


def first_size_gb(text: str) -> float | None:
    m = _SIZE.search(text or "")
    return _to_gb(m.group(1), m.group(2)) if m else None


def all_sizes_gb(text: str) -> list[float]:
    return [_to_gb(a, b) for a, b in _SIZE.findall(text or "")]


def explicit_vram_gb(text: str) -> float | None:
    """'VRAM 6 GB', '4GB+ of VRAM', '2 GB video memory', '128mb Video Memory'."""
    t = text or ""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(gb|mb)\+?\s*(?:of\s+)?(?:dedicated\s+)?(?:vram|video\s*memory|video\s*ram|graphics\s*memory)", t, re.I)
    if m:
        return _to_gb(m.group(1), m.group(2))
    m = re.search(r"(?:vram|video\s*memory)\s*[:\-]?\s*(?:of\s+)?(\d+(?:[.,]\d+)?)\s*(gb|mb)", t, re.I)
    if m:
        return _to_gb(m.group(1), m.group(2))
    return None


def ssd_requirement(tier: RawTier) -> str | None:
    """Returns 'required', 'recommended' or None."""
    text = " ".join(tier.fields.get(k, "") for k in ("storage", "notes")).lower()
    if "ssd" not in text:
        return None
    if re.search(r"ssd[^.;,]{0,25}(recommended|preferred|suggested|advised)|recommended[^.;,]{0,20}ssd|hdd supported", text):
        return "recommended"
    return "required"


def split_alternatives(text: str) -> list[str]:
    """'GTX 1060 6GB or RX 580 / Arc A380' -> ['GTX 1060 6GB', 'RX 580', 'Arc A380']."""
    t = re.sub(r"\([^)]*\)", lambda m: m.group(0) if re.search(r"\d\s*(gb|mb)", m.group(0), re.I) else " ", text)
    parts = re.split(r"\s*(?:,|/|\||;|\bor\b|\band\b|\s[-–]\s|\bvs\.?)\s*", t, flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]
