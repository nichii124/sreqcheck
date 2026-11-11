"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, Prompt

from . import __version__, hardware, protondb
from .analysis import Resolver, analyze, choose_gpu, resolve_own_cpu
from .online import OnlineLookup
from .report import render, render_specs, slugify, to_markdown
from .steam import SteamError, fetch_game, parse_app_id, parse_game_requirements

DEFAULT_REPORT_DIR = Path.home() / "steam-reports"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sreqcheck",
        description="Compare this Linux PC with a Steam game's Windows system requirements "
                    "(for playing through Proton).",
    )
    p.add_argument("game", nargs="?", help="Steam store URL or app ID, e.g. 1091500")
    p.add_argument("--proton-overhead", nargs="?", type=float, const=10.0, default=0.0, metavar="PERCENT",
                   help="raise CPU/GPU requirements to allow for Proton's overhead "
                        "(default when given without a value: 10)")
    p.add_argument("--save", nargs="?", const="", default=None, metavar="PATH",
                   help=f"save the report as Markdown (default folder: {DEFAULT_REPORT_DIR})")
    p.add_argument("--no-save", action="store_true", help="don't ask about saving the report")
    p.add_argument("--offline", action="store_true",
                   help="don't look up unknown CPUs/GPUs online (Steam and ProtonDB are still contacted)")
    p.add_argument("--no-protondb", action="store_true", help="skip the ProtonDB check")
    p.add_argument("--refresh-cache", action="store_true", help="re-download cached online benchmark data")
    p.add_argument("--specs", action="store_true", help="only show the detected hardware")
    g = p.add_argument_group("override detected hardware (useful if detection gets something wrong)")
    g.add_argument("--cpu", metavar="NAME", help='e.g. "Ryzen 5 5600X"')
    g.add_argument("--gpu", metavar="NAME", help='e.g. "RTX 3060"')
    g.add_argument("--ram", type=float, metavar="GB", help="installed RAM in GB")
    g.add_argument("--vram", type=float, metavar="GB", help="graphics card memory in GB")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _save(markdown: str, game_name: str, target: str, console: Console) -> None:
    path = Path(target).expanduser() if target else DEFAULT_REPORT_DIR / f"{slugify(game_name)}.md"
    if path.is_dir() or (target and target.endswith("/")):
        path = path / f"{slugify(game_name)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    console.print(f"[green]Report saved to[/green] {path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    online = OnlineLookup(enabled=not args.offline, refresh=args.refresh_cache)

    with console.status("Detecting hardware…"):
        specs = hardware.detect()

    if args.specs or not args.game:
        resolver = Resolver(online)
        with console.status("Identifying parts…"):
            cpu = resolve_own_cpu(specs, resolver, args.cpu)
            _, _, gpus = choose_gpu(specs, resolver, None)
        render_specs(specs, {"cpu": cpu, "gpus": {i: r for i, (_, r) in enumerate(gpus)}}, console)
        if not args.game and not args.specs:
            console.print("\n[dim]Pass a Steam store URL or app ID to check a game, e.g.[/dim] "
                          "sreqcheck https://store.steampowered.com/app/1091500/")
        return 0

    try:
        app_id = parse_app_id(args.game)
        with console.status("Fetching requirements from Steam…"):
            game = fetch_game(app_id)
    except SteamError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 2

    minimum, recommended = parse_game_requirements(game)
    if minimum.empty and recommended.empty:
        console.print(f"[yellow]{game.name} doesn't list any Windows system requirements on Steam.[/yellow]")
        return 1
    if minimum.empty:  # only one column given; treat it as the minimum
        minimum, recommended = recommended, minimum

    proton = None
    if not args.no_protondb:
        with console.status("Checking ProtonDB…"):
            proton = protondb.fetch(app_id)

    with console.status("Comparing…"):
        result = analyze(
            game, minimum, recommended, specs, proton, online,
            proton_overhead=max(0.0, args.proton_overhead) / 100,
            cpu_override=args.cpu, gpu_override=args.gpu,
            ram_override=args.ram, vram_override=args.vram,
        )
    render(result, console)

    if args.save is not None:
        _save(to_markdown(result), game.name, args.save, console)
    elif not args.no_save and sys.stdin.isatty() and sys.stdout.isatty():
        console.print()
        if Confirm.ask("Save this report as Markdown?", default=False, console=console):
            default = str(DEFAULT_REPORT_DIR / f"{slugify(game.name)}.md")
            target = Prompt.ask("File", default=default, console=console)
            _save(to_markdown(result), game.name, target, console)
    return 0


if __name__ == "__main__":
    sys.exit(main())
