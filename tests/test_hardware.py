from steam_reqcheck import hardware as hw

VULKANINFO_SUMMARY = """
==========
VULKANINFO
==========

Devices:
========
GPU0:
	apiVersion         = 1.3.274
	driverVersion      = 24.0.5
	vendorID           = 0x1002
	deviceID           = 0x73ff
	deviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
	deviceName         = AMD Radeon RX 6600 (RADV NAVI23)
GPU1:
	apiVersion         = 1.3.274
	driverVersion      = 0.0.1
	vendorID           = 0x10005
	deviceID           = 0x0000
	deviceType         = PHYSICAL_DEVICE_TYPE_CPU
	deviceName         = llvmpipe (LLVM 17.0.6, 256 bits)
"""

LSPCI = '03:00.0 "VGA compatible controller [0300]" "Advanced Micro Devices, Inc. [AMD/ATI] [1002]" ' \
        '"Navi 23 [Radeon RX 6600/6600 XT/6600M] [73ff]" -rc7 "XFX Limited [1682]" "Device [5000]"\n' \
        '00:02.0 "VGA compatible controller [0300]" "Intel Corporation [8086]" ' \
        '"CometLake-U GT2 [UHD Graphics] [9b41]" -r02 "Lenovo [17aa]" "Device [5089]"\n' \
        '00:1f.3 "Audio device [0403]" "Intel Corporation [8086]" "Comet Lake PCH-LP cAVS [02c8]"\n'


def test_vulkaninfo_summary_parsing():
    devs = hw._parse_vulkaninfo_summary(VULKANINFO_SUMMARY)
    assert devs[0]["deviceName"] == "AMD Radeon RX 6600 (RADV NAVI23)"
    assert devs[1]["deviceType"].endswith("CPU")


def test_lspci_parsing():
    devs = hw._parse_lspci(LSPCI)
    assert len(devs) == 2  # audio device skipped
    name, ambiguous, integrated = hw._lspci_friendly(devs[0])
    assert name == "AMD Radeon RX 6600" and ambiguous and integrated is False
    name, _, integrated = hw._lspci_friendly(devs[1])
    assert name == "Intel UHD Graphics 620" and integrated


def test_gpu_detection_merges_sources(monkeypatch):
    outputs = {"vulkaninfo": VULKANINFO_SUMMARY, "lspci": LSPCI, "nvidia-smi": None}
    monkeypatch.setattr(hw, "_run", lambda cmd, timeout=20: outputs.get(cmd[0]) if cmd != ["vulkaninfo"] else "")
    monkeypatch.setattr(hw.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(hw, "_vram_from_sysfs", lambda addr: 8.0 if addr == "0000:03:00.0" else None)
    specs = hw.SystemSpecs()
    hw._detect_gpus(specs)
    names = {g.name: g for g in specs.gpus}
    assert names["AMD Radeon RX 6600"].vulkan is True
    assert names["AMD Radeon RX 6600"].vram_gb == 8.0
    assert names["Intel UHD Graphics 620"].vulkan is False  # no Vulkan device for it


def test_friendly_names():
    assert hw.friendly_gpu_name("AMD Radeon Graphics (RADV RENOIR)") == "AMD Radeon Vega 7"
    assert hw.friendly_gpu_name("AMD Custom GPU 0405 (RADV VANGOGH)") == "Steam Deck GPU"
    assert hw.friendly_gpu_name("Intel(R) Graphics (RPL-P)") == "Intel Iris Xe Graphics"
    assert hw.friendly_gpu_name("NVIDIA GeForce RTX 3060 (NVK GA106)") == "NVIDIA GeForce RTX 3060"


def test_ram_estimate():
    assert hw.installed_ram_estimate(15.4) == 16
    assert hw.installed_ram_estimate(7.6) == 8
    assert hw.installed_ram_estimate(13.6) == 16
    assert hw.installed_ram_estimate(31.2) == 32


def test_libraryfolders_vdf():
    text = '''"libraryfolders"
{
	"0"
	{
		"path"		"/home/me/.local/share/Steam"
		"apps"
		{
			"228980"		"123"
		}
	}
	"1"
	{
		"path"		"/mnt/games/SteamLibrary"
		"apps"
		{
		}
	}
}'''
    data = hw.parse_vdf(text)
    assert data["libraryfolders"]["1"]["path"] == "/mnt/games/SteamLibrary"
    assert "228980" in data["libraryfolders"]["0"]["apps"]
