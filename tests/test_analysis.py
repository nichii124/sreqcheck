from steam_reqcheck.analysis import Status, analyze
from steam_reqcheck.report import to_markdown
from steam_reqcheck.steam import parse_game_requirements

from conftest import load_game, make_specs, rating


def run(app_id, specs, offline, tier="platinum", **kw):
    game = load_game(app_id)
    mn, rec = parse_game_requirements(game)
    return analyze(game, mn, rec, specs, rating(tier), offline, **kw)


def comp(a, key):
    return next(c for c in a.components if c.key == key)


def test_strong_pc_meets_recommended(offline):
    a = run(2050650, make_specs(cpu="AMD Ryzen 7 7800X3D 8-Core Processor",
                                gpu="AMD Radeon RX 7800 XT (RADV NAVI32)", vram=16, ram=32), offline)
    assert a.verdict == "Meets recommended"
    assert a.chance == 100
    assert all(c.minimum.status is Status.PASS for c in a.components if c.key != "storage")


def test_user_example_ram_below_minimum(offline):
    """Game wants 16 GB minimum, PC has 8 GB -> minimum memory not met."""
    a = run(2358720, make_specs(ram=8), offline)  # Black Myth: Wukong, 16 GB minimum
    ram = comp(a, "ram")
    assert ram.minimum.status is Status.FAIL
    assert "8 GB" in ram.minimum.detail and "16 GB" in ram.minimum.detail
    assert a.verdict == "Below minimum"
    assert 0 < a.chance < 100
    assert any("Memory" in r for r in a.chance_reasons)
    assert any(s.component == "Memory" for s in a.suggestions)


def test_user_example_ram_meets_minimum_not_recommended(offline):
    """Minimum 8 GB, recommended 16 GB, PC has 8 GB."""
    a = run(2050650, make_specs(ram=8), offline)  # Resident Evil 4
    ram = comp(a, "ram")
    assert ram.minimum.status is Status.PASS
    assert ram.recommended.status is Status.FAIL
    assert a.verdict == "Meets minimum"


def test_no_vulkan_is_a_hard_blocker(offline):
    a = run(367520, make_specs(vulkan=False), offline)
    assert comp(a, "vulkan").minimum.status is Status.FAIL
    assert a.chance == 0 and a.verdict == "Won't run"


def test_borked_is_a_hard_blocker(offline):
    a = run(1245620, make_specs(), offline, tier="borked")
    assert a.chance == 0


def test_far_too_weak_gpu_is_a_hard_blocker(offline):
    a = run(1091500, make_specs(gpu="Intel(R) HD Graphics 4000", vram=None, integrated=True), offline)
    gpu = comp(a, "gpu")
    assert gpu.minimum.status is Status.FAIL and gpu.minimum.hard_blocker
    assert a.chance == 0


def test_slightly_weak_gpu_lowers_chance_gradually(offline):
    # Cyberpunk's easiest minimum GPU option is the Arc A380
    weak = run(1091500, make_specs(gpu="NVIDIA GeForce GTX 1050 Ti", vram=6), offline)
    weaker = run(1091500, make_specs(gpu="NVIDIA GeForce GTX 750 Ti", vram=6), offline)
    assert 0 < weaker.chance < weak.chance < 100


def test_storage_is_reported_separately(offline):
    a = run(1086940, make_specs(free_gb=20), offline)  # BG3 needs 150 GB
    assert comp(a, "storage").minimum.status is Status.FAIL
    assert "Not enough room" in a.storage_note
    assert a.chance == 100  # disk space doesn't change the chance


def test_already_installed(offline):
    a = run(1086940, make_specs(free_gb=1, installed=[1086940]), offline)
    assert comp(a, "storage").minimum.status is Status.PASS


def test_ssd_required_on_hdd(offline):
    a = run(1091500, make_specs(ssd=False), offline)
    st = comp(a, "storage").minimum
    assert st.status is Status.FAIL and "SSD" in st.detail
    assert a.chance < 100


def test_proton_overhead_option(offline):
    specs = make_specs(gpu="NVIDIA GeForce GTX 1650", vram=8)  # ~13% above Cyberpunk's easiest option
    plain = run(1091500, specs, offline)
    stricter = run(1091500, specs, offline, proton_overhead=0.30)
    assert comp(plain, "gpu").minimum.status is Status.PASS
    assert comp(stricter, "gpu").minimum.status is Status.FAIL


def test_dual_gpu_laptop_uses_the_stronger_gpu(offline):
    from steam_reqcheck.hardware import GPUInfo
    specs = make_specs(laptop=True)
    specs.gpus = [
        GPUInfo("Intel Iris Xe Graphics", "intel", integrated=True, vulkan=True, source="test"),
        GPUInfo("NVIDIA GeForce RTX 3060 Laptop GPU", "nvidia", integrated=False, vram_gb=6, vulkan=True,
                source="test"),
    ]
    a = run(1091500, specs, offline)
    assert "3060" in comp(a, "gpu").yours
    assert any("Several GPUs" in n for n in a.notes)


def test_integrated_gpu_vram_is_shared(offline):
    a = run(367520, make_specs(gpu="AMD Radeon 780M", vram=None, integrated=True), offline)
    assert "shared" in comp(a, "vram").yours


def test_overrides(offline):
    a = run(1091500, make_specs(), offline, cpu_override="i7-12700K", gpu_override="RTX 4080", ram_override=32,
            vram_override=16)
    assert a.verdict == "Meets recommended"


def test_markdown_report(offline):
    md = to_markdown(run(1091500, make_specs(ram=8), offline))
    assert md.startswith("# Cyberpunk 2077")
    assert "| **Memory** |" in md and "❌" in md
