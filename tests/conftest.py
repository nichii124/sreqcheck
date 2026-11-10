import json
from pathlib import Path

import pytest

from steam_reqcheck.hardware import GPUInfo, LibraryInfo, SystemSpecs
from steam_reqcheck.online import OnlineLookup
from steam_reqcheck.protondb import ProtonRating
from steam_reqcheck.steam import Game

FIXTURES = Path(__file__).parent / "fixtures"


def load_game(app_id: int) -> Game:
    data = json.loads((FIXTURES / f"appdetails_{app_id}.json").read_text())[str(app_id)]["data"]
    reqs = data["pc_requirements"] or {}
    return Game(app_id, data["name"], reqs.get("minimum", ""), reqs.get("recommended", ""),
                bool(data["platforms"].get("linux")), store_url=f"https://store.steampowered.com/app/{app_id}/")


def make_specs(cpu="AMD Ryzen 5 5600X 6-Core Processor", gpu="AMD Radeon RX 6600 (RADV NAVI23)",
               vram=8.0, ram=16.0, vulkan=True, integrated=False, free_gb=500.0, ssd=True,
               laptop=False, installed=()):
    return SystemSpecs(
        cpu_name=cpu, cpu_cores=6, cpu_threads=12, ram_usable_gb=ram - 0.4, ram_installed_gb=ram,
        gpus=[GPUInfo(name=gpu, vendor="amd", integrated=integrated, vram_gb=vram, vulkan=vulkan,
                      vulkan_version="1.3.274", source="test")],
        arch="x86_64", is_64bit=True, is_laptop=laptop, vulkaninfo_available=True,
        libraries=[LibraryInfo("/home/test/.local/share/Steam", free_gb, 1000, ssd, "/dev/nvme0n1p2",
                               set(installed))],
    )


@pytest.fixture
def offline():
    return OnlineLookup(enabled=False)


def rating(tier):
    return ProtonRating(tier=tier, overall_tier=tier, trending_tier=tier, total_reports=100,
                        url="https://www.protondb.com/app/1")
