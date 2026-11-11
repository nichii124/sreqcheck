"""Rendering an Analysis to the terminal (rich) and to Markdown."""

from __future__ import annotations

import datetime as _dt
import re

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .analysis import Analysis, Check, Status, fmt_gb
from .hardware import SystemSpecs

ICON = {Status.PASS: "✅", Status.FAIL: "❌", Status.UNKNOWN: "❔", Status.NOT_LISTED: "—"}
STYLE = {Status.PASS: "green", Status.FAIL: "red", Status.UNKNOWN: "yellow", Status.NOT_LISTED: "dim"}
VERDICT_STYLE = {"Won't run": "bold white on red", "Below minimum": "bold red",
                 "Meets minimum": "bold yellow", "Meets recommended": "bold green"}
TIER_STYLE = {"platinum": "bold bright_white", "gold": "bold yellow", "silver": "bold white",
              "bronze": "bold dark_orange", "borked": "bold red"}


def _chance_style(pct: int) -> str:
    return "bold green" if pct >= 85 else "bold yellow" if pct >= 50 else "bold red"


def _cell(check: Check) -> Text:
    t = Text()
    if check.status is Status.NOT_LISTED:
        t.append("—", style="dim")
        return t
    t.append(ICON[check.status] + " ", style=STYLE[check.status])
    t.append(check.required or "", style=STYLE[check.status])
    if check.estimated and check.status is Status.PASS:
        t.append(" (est.)", style="dim")
    if check.detail and check.status is not Status.NOT_LISTED:
        t.append("\n" + check.detail, style="dim")
    return t


def render(a: Analysis, console: Console) -> None:
    title = Text.assemble((a.game.name, "bold"), ("  ·  ", "dim"), (a.game.store_url, "dim underline"))
    console.print()
    console.print(title)

    table = Table(box=box.ROUNDED, show_lines=True, expand=True)
    table.add_column("Component", style="bold", no_wrap=True)
    table.add_column("Your PC", ratio=2, overflow="fold")
    table.add_column("Minimum", ratio=3, overflow="fold")
    if a.has_recommended:
        table.add_column("Recommended", ratio=3, overflow="fold")
    for c in a.components:
        yours = Text(c.yours)
        if c.yours_estimated:
            yours.append(" (est.)", style="dim")
        row = [c.label, yours, _cell(c.minimum)]
        if a.has_recommended:
            row.append(_cell(c.recommended))
        table.add_row(*row)
    console.print(table)

    # Summary
    lines: list = []
    lines.append(Text.assemble(("Verdict: ", "bold"), (a.verdict, VERDICT_STYLE.get(a.verdict, "bold")),
                               (" — " + a.verdict_detail, "")))
    lines.append(Text.assemble(("Chance it runs: ", "bold"), (f"{a.chance}%", _chance_style(a.chance)),
                               (f"   (confidence: {a.confidence})", "dim")))
    for r in a.chance_reasons:
        lines.append(Text("  • " + r))
    if a.proton:
        lines.append(_proton_text(a))
    if a.storage_note:
        lines.append(Text.assemble(("Storage: ", "bold"), a.storage_note))
    console.print(Panel(Group(*lines), title="Summary", title_align="left", border_style="cyan"))

    if a.suggestions:
        sug = [Text.assemble((f"{s.component}: ", "bold"), s.text) for s in a.suggestions]
        console.print(Panel(Group(*sug), title="Upgrade suggestions", title_align="left", border_style="magenta"))

    if a.notes:
        console.print(Panel(Group(*[Text("• " + n, style="dim") for n in a.notes]), title="Notes",
                            title_align="left", border_style="dim"))
    console.print(Text("Scores are approximate. \"(est.)\" marks values that were estimated rather than "
                       "looked up directly.", style="dim italic"))


def _proton_text(a: Analysis) -> Text:
    p = a.proton
    t = Text.assemble(("ProtonDB: ", "bold"))
    if p.error:
        t.append(f"couldn't be reached ({p.error})", style="yellow")
        return t
    if not p.tier:
        t.append("no reports yet", style="yellow")
        return t
    t.append(p.tier.capitalize(), style=TIER_STYLE.get(p.tier, "bold"))
    t.append(f" ({p.meaning})")
    extra = [f"{p.total_reports} reports"]
    if p.trending_tier and p.trending_tier != p.overall_tier:
        extra.append(f"overall {p.overall_tier}, recently {p.trending_tier}")
    t.append("  " + ", ".join(extra), style="dim")
    return t


def render_specs(specs: SystemSpecs, resolved: dict, console: Console) -> None:
    t = Table(box=box.ROUNDED, title="Detected hardware", show_header=False)
    t.add_column(style="bold")
    t.add_column()
    t.add_row("CPU", f"{specs.cpu_name}  ({specs.cpu_cores} cores / {specs.cpu_threads} threads"
                     + (f", up to {specs.cpu_max_ghz:.2f} GHz" if specs.cpu_max_ghz else "") + ")")
    if resolved.get("cpu"):
        r = resolved["cpu"]
        t.add_row("", Text(f"→ {r.name}, score {r.score:,.0f} ({r.how})", style="dim"))
    t.add_row("RAM", f"{fmt_gb(specs.ram_installed_gb)} installed (estimated), "
                     f"{specs.ram_usable_gb or 0:.1f} GB usable")
    for i, g in enumerate(specs.gpus):
        kind = "integrated" if g.integrated else "dedicated" if g.integrated is False else "type unknown"
        vk = {True: f"Vulkan {g.vulkan_version or 'yes'}", False: "no Vulkan", None: "Vulkan unknown"}[g.vulkan]
        t.add_row(f"GPU {i}", f"{g.name}  ({kind}, VRAM {fmt_gb(g.vram_gb)}, {vk}; via {g.source})")
        r = resolved.get("gpus", {}).get(i)
        if r:
            t.add_row("", Text(f"→ {r.name}, score {r.score:,.0f} ({r.how})", style="dim"))
    if not specs.gpus:
        t.add_row("GPU", "none detected")
    t.add_row("System", f"{specs.arch}, " + ("laptop/handheld" if specs.is_laptop else "desktop"
                                            if specs.is_laptop is False else "form factor unknown")
              + (f" ({specs.device_label})" if specs.device_label else ""))
    for lib in specs.libraries:
        kind = "SSD" if lib.ssd else "HDD" if lib.ssd is False else "drive type unknown"
        t.add_row("Library", f"{lib.path}: {fmt_gb(lib.free_gb)} free of {fmt_gb(lib.total_gb)} ({kind})")
    if not specs.libraries:
        t.add_row("Library", "no Steam library found")
    console.print(t)
    for n in specs.notes:
        console.print(Text("• " + n, style="dim"))


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def _md_cell(check: Check) -> str:
    if check.status is Status.NOT_LISTED:
        return "—"
    s = f"{ICON[check.status]} {_md_escape(check.required)}"
    if check.estimated and check.status is Status.PASS:
        s += " *(est.)*"
    if check.detail:
        s += f"<br><sub>{_md_escape(check.detail)}</sub>"
    return s


def to_markdown(a: Analysis) -> str:
    out = [f"# {a.game.name}: system requirements check", ""]
    out.append(f"[Steam store page]({a.game.store_url}) · App ID {a.game.app_id} · "
               f"checked {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    out.append("")
    out.append(f"**Verdict:** {a.verdict} — {a.verdict_detail}  ")
    out.append(f"**Chance it runs:** {a.chance}% (confidence: {a.confidence})")
    out.append("")
    for r in a.chance_reasons:
        out.append(f"- {r}")
    out.append("")
    head = "| Component | Your PC | Minimum |" + (" Recommended |" if a.has_recommended else "")
    sep = "|---|---|---|" + ("---|" if a.has_recommended else "")
    out += [head, sep]
    for c in a.components:
        yours = _md_escape(c.yours) + (" *(est.)*" if c.yours_estimated else "")
        row = f"| **{c.label}** | {yours} | {_md_cell(c.minimum)} |"
        if a.has_recommended:
            row += f" {_md_cell(c.recommended)} |"
        out.append(row)
    out.append("")
    if a.proton:
        p = a.proton
        out.append("## ProtonDB")
        out.append("")
        if p.error:
            out.append(f"Couldn't be reached ({p.error}).")
        elif not p.tier:
            out.append(f"No reports yet. <{p.url}>")
        else:
            line = f"**{p.tier.capitalize()}**: {p.meaning} ({p.total_reports} reports"
            if p.trending_tier and p.trending_tier != p.overall_tier:
                line += f"; overall {p.overall_tier}, recently {p.trending_tier}"
            out.append(line + f"). <{p.url}>")
        out.append("")
    if a.storage_note:
        out += ["## Storage", "", a.storage_note, ""]
    if a.suggestions:
        out += ["## Upgrade suggestions", ""]
        out += [f"- **{s.component}:** {s.text}" for s in a.suggestions]
        out.append("")
    if a.notes:
        out += ["## Notes", ""]
        out += [f"- {n}" for n in a.notes]
        out.append("")
    out.append("<sub>Generated by sreqcheck. Scores are approximate; *(est.)* marks estimated values.</sub>")
    return "\n".join(out) + "\n"


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name).strip().lower()
    return re.sub(r"[\s_-]+", "-", s) or "game"
