import pytest

from steam_reqcheck.analysis import Resolver, _vram_needed
from steam_reqcheck.steam import (SteamError, first_size_gb, parse_app_id, parse_game_requirements,
                                  parse_requirements_html, split_alternatives, ssd_requirement)

from conftest import load_game


@pytest.mark.parametrize("text,app_id", [
    ("1091500", 1091500),
    ("https://store.steampowered.com/app/1091500/Cyberpunk_2077/", 1091500),
    ("store.steampowered.com/app/730", 730),
    ("steam://store/570", 570),
])
def test_parse_app_id(text, app_id):
    assert parse_app_id(text) == app_id


def test_parse_app_id_rejects_garbage():
    with pytest.raises(SteamError):
        parse_app_id("cyberpunk")


def test_cyberpunk():
    mn, rec = parse_game_requirements(load_game(1091500))
    assert mn.requires_64bit
    assert first_size_gb(mn.fields["memory"]) == 12
    assert first_size_gb(rec.fields["memory"]) == 16
    assert first_size_gb(mn.fields["storage"]) == 70
    assert ssd_requirement(mn) == "required"
    assert _vram_needed(mn) == 6
    assert "directx" in mn.fields  # parsed, but ignored by the comparison


def test_witcher_vram_from_notes():
    mn, rec = parse_game_requirements(load_game(292030))
    assert _vram_needed(mn) == 6 and _vram_needed(rec) == 8
    assert ssd_requirement(mn) == "required"


def test_terraria_old_style_labels():
    mn, rec = parse_game_requirements(load_game(105600))
    assert first_size_gb(mn.fields["memory"]) == 2.5
    assert first_size_gb(mn.fields["storage"]) == pytest.approx(200 / 1024)
    assert _vram_needed(mn) == pytest.approx(128 / 1024)
    assert "Dual Core" in rec.fields["processor"]


def test_single_column_games():
    mn, rec = parse_game_requirements(load_game(413150))
    assert not mn.empty and rec.empty


def test_combined_minimum_and_recommended_block():
    html = ("<strong>Minimum:</strong><br><ul><li><strong>Memory:</strong> 8 GB RAM</li></ul>"
            "<strong>Recommended:</strong><br><ul><li><strong>Memory:</strong> 16 GB RAM</li></ul>")
    mn, rec = parse_requirements_html(html)
    assert mn.fields["memory"] == "8 GB RAM" and rec.fields["memory"] == "16 GB RAM"


def test_split_alternatives():
    assert split_alternatives("GTX 1060 6GB or RX 580 8GB / Arc A380") == ["GTX 1060 6GB", "RX 580 8GB", "Arc A380"]


def test_every_fixture_resolves(offline):
    """All 13 saved games: every CPU/GPU line resolves to at least one part."""
    import glob, os, re
    from conftest import FIXTURES
    r = Resolver(offline)
    for path in glob.glob(str(FIXTURES / "appdetails_*.json")):
        app_id = int(re.search(r"(\d+)", os.path.basename(path)).group(1))
        for tier in parse_game_requirements(load_game(app_id)):
            if tier.empty:
                continue
            parts, _ = r.cpu_requirement(tier.fields["processor"])
            assert parts, (app_id, tier.fields["processor"])
            gfx = tier.fields["graphics"]
            parts, unknown = r.gpu_requirement(gfx)
            assert parts or not unknown, (app_id, gfx)


def test_prefix_borrowing(offline):
    r = Resolver(offline)
    parts, _ = r.gpu_requirement("NVIDIA GTX 970/980")
    assert {p.name for p in parts} == {"NVIDIA GeForce GTX 970", "NVIDIA GeForce GTX 980"}
    parts, _ = r.cpu_requirement("Intel Core i5-4460/4590")
    assert {p.name for p in parts} == {"Intel Core i5-4460", "Intel Core i5-4590"}
