import pytest

from steam_reqcheck import benchmarks as bm


@pytest.mark.parametrize("name,expected", [
    ("Intel(R) Core(TM) i5-4460  CPU @ 3.20GHz", "Intel Core i5-4460"),
    ("AMD Ryzen 5 5600X 6-Core Processor", "AMD Ryzen 5 5600X"),
    ("12th Gen Intel(R) Core(TM) i7-12700H", "Intel Core i7-12700H"),
    ("AMD Ryzen 7 5800H with Radeon Graphics", "AMD Ryzen 7 5800H"),
    ("AMD Ryzen 5 2400G with Radeon Vega Graphics", "AMD Ryzen 5 2400G"),
    ("INTEL CORE I5-8400", "Intel Core i5-8400"),
    ("AMD r5 3600", "AMD Ryzen 5 3600"),
    ("Intel I5 4690", "Intel Core i5-4690"),
    ("AMD FX 8350", "AMD FX-8350"),
    ("Core 2 Duo E8400 3.0GHz", "Intel Core 2 Duo E8400"),
    ("AMD Custom APU 0405", "AMD Custom APU 0405"),
])
def test_cpu_names(name, expected):
    m = bm.match_cpu(name)
    assert m and m.entry.name == expected


@pytest.mark.parametrize("name,expected", [
    ("NVIDIA GeForce GTX 1060 6GB", "NVIDIA GeForce GTX 1060"),
    ("NVIDIA GEFORCE GTX 1060 3 GB", "NVIDIA GeForce GTX 1060"),
    ("GTX 1050ti", "NVIDIA GeForce GTX 1050 Ti"),
    ("NVIDIA GeForce RTX 3060 Laptop GPU", "NVIDIA GeForce RTX 3060 Laptop GPU"),
    ("AMD Radeon RX 6600 (RADV NAVI23)", "AMD Radeon RX 6600"),
    ("AMD Radeon RX 6600XT", "AMD Radeon RX 6600 XT"),
    ("AMD Radeon™ R9 290", "AMD Radeon R9 290"),
    ("Intel® Arc™ A580", "Intel Arc A580"),
    ("Arc A380", "Intel Arc A380"),
    ("Intel HD 4000", "Intel HD Graphics 4000"),
    ("Nvidia 2060 Super", "NVIDIA GeForce RTX 2060 Super"),
    ("RTX 2070 with Max-Q Design", "NVIDIA GeForce RTX 2070 Mobile"),
    ("Radeon VII", "AMD Radeon VII"),
])
def test_gpu_names(name, expected):
    m = bm.match_gpu(name)
    assert m and m.entry.name == expected


@pytest.mark.parametrize("name", ["Radeon R7 360", "Intel Arc A570M", "DirectX 11", "Shader Model 5.0"])
def test_no_false_matches(name):
    assert bm.match_gpu(name) is None


def test_more_specific_entry_wins():
    assert bm.match_gpu("RTX 3060 Ti").entry.name == "NVIDIA GeForce RTX 3060 Ti"
    assert bm.match_gpu("RX 7900 XTX").entry.name == "AMD Radeon RX 7900 XTX"


def test_laptop_cpu_matched_to_desktop_is_flagged():
    m = bm.match_cpu("Intel Core i7-4770HQ")
    assert m.entry.name == "Intel Core i7-4770" and m.laptop_penalty


def test_laptop_preference_for_old_nvidia_names():
    assert bm.match_gpu("NVIDIA GeForce GTX 1060", prefer_laptop=True).entry.name == "NVIDIA GeForce GTX 1060 Mobile"
    assert bm.match_gpu("NVIDIA GeForce GTX 1060").entry.name == "NVIDIA GeForce GTX 1060"


def test_family_and_generic():
    entry, label = bm.cpu_family_representative("Intel Core i5 4th generation")
    assert entry.name == "Intel Core i5-4460"
    entry, _ = bm.cpu_family_representative("Ryzen 5 CPU or Equivalent")
    assert entry.name == "AMD Ryzen 5 1600"
    index, cores, ghz = bm.generic_cpu_requirement("Dual core from Intel or AMD at 2.8 GHz")
    assert cores == 2 and ghz == 2.8 and index > 0


def test_tables_are_sane():
    for e in bm.cpu_table():
        assert 0 < e.single <= e.multi * 1.3, e.name
    names = [e.name for e in bm.gpu_table()]
    assert len(names) == len(set(names))
    # every entry must be findable by its own name
    for e in bm.gpu_table():
        assert bm.match_gpu(e.name).entry.name == e.name, e.name
    for e in bm.cpu_table():
        assert bm.match_cpu(e.name).entry.name == e.name, e.name
