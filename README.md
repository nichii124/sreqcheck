# sreqcheck

Checks whether your Linux PC can run a Steam game through Proton. It compares your hardware with the game's
**Windows** system requirements on Steam, checking **Minimum** and **Recommended** separately, and tells you:

- which requirements you meet and which you don't, part by part (CPU, GPU, video memory, RAM, storage)
- an overall verdict: *Won't run*, *Below minimum*, *Meets minimum* or *Meets recommended*
- the **chance the game runs**, as a percentage with the reasons behind it
- the game's **ProtonDB** rating
- **upgrade suggestions** for anything that falls short
- optionally, a saved **Markdown report**

## Install

You need Python 3.10 or newer. These tools let the program detect more of your hardware. Most distros
already have them; if not, install them with your package manager:

| Tool | Package | Used for |
|---|---|---|
| `vulkaninfo` | `vulkan-tools` | exact GPU names and video memory |
| `lspci` | `pciutils` | finding every GPU |
| `nvidia-smi` | comes with the NVIDIA driver | NVIDIA video memory |

Then install the program:

```bash
git clone https://github.com/nichii124/sreqcheck
cd sreqcheck
pipx install .          # or: python3 -m pip install --user .
```

## Use

```bash
sreqcheck https://store.steampowered.com/app/1091500/Cyberpunk_2077/
sreqcheck 1091500               # the app ID works too
sreqcheck --specs               # just show what hardware was detected
```

After the report, you're asked whether to save it as Markdown. By default it goes in `~/steam-reports/<game>.md`.

| Option | What it does |
|---|---|
| `--proton-overhead [PCT]` | Makes the CPU/GPU checks stricter to allow for Proton's overhead (10% if no value is given). Off by default. |
| `--save [PATH]` / `--no-save` | Save without asking (to PATH or the default folder) / don't ask |
| `--offline` | Don't look up unknown CPUs/GPUs online (Steam and ProtonDB are still contacted) |
| `--no-protondb` | Skip the ProtonDB check |
| `--cpu`, `--gpu`, `--ram GB`, `--vram GB` | Override detected hardware, e.g. `--gpu "RX 6600"` if detection got it wrong, or to try out an upgrade |
| `--refresh-cache` | Re-download the cached online benchmark data |

## How it works

**Requirements** come from Steam's store API (`appdetails`), in English. The program always uses the Windows
column. The OS line is only checked for 64-bit, because Proton takes care of the Windows version. DirectX,
network, sound and notes lines are ignored, except that the notes are searched for "SSD required" and VRAM
amounts.

**Your hardware** comes from `/proc/cpuinfo`, `/proc/meminfo`, `vulkaninfo`, `lspci`, `nvidia-smi` and sysfs.
If there is more than one GPU (common on laptops), the strongest one is used. Steam libraries are read from
`libraryfolders.vdf`, and for each library the program checks free space and whether the drive is an SSD.

**CPUs and GPUs** are compared using benchmark scores in `src/sreqcheck/data/cpus.csv` and `gpus.csv`
(about 300 of each). The scores are approximate, on a PassMark-like scale, and you can edit the files. The CPU
score weights single-thread speed at 65% and all-core speed at 35%, because games depend more on per-core speed.
When a requirement lists alternatives ("GTX 1060 or RX 580"), you pass if you match or beat **either** one. A
part counts as matching at 95% of the listed part's score.

**Video memory**: if the store page states an amount ("VRAM 6 GB", "GTX 1060 6GB", "4GB+ of VRAM"), that
amount is used. If the page only names cards, each card's VRAM is looked up in `gpus.csv`. For example, The
Last Flame's minimum lists only "GTX 960 or R9 380", so it uses the GTX 960's 2 GB. The table stores each card's
**smallest common variant**: the GTX 960 came with 2 or 4 GB, so it's recorded as 2 GB. That means you're
never told you lack VRAM for a card the developer named. As with speed, matching the easiest listed card is
enough. The report says where each number came from ("GTX 960 has 2 GB" or "listed on the store page"). If
your own card's VRAM can't be detected, the table's value for it is used and marked *(est.)*.

If a part isn't in the tables, it's looked up in **[Blender Open Data](https://opendata.blender.org)**, a public
benchmark database. Its rendering scores are converted to the bundled scale using the parts the two datasets
have in common, so these values are rough (typically within about ±20%) and are marked *(est.)*. The downloaded
data is cached for 30 days in `~/.cache/sreqcheck/`.

Vague requirements are handled as follows:
- "Intel Core i5 4th gen" or "Ryzen 5" is compared against a typical part from that family.
- "2.4 GHz dual core" is estimated from the clock speed and core count.

All of these show *(est.)* in the report.

### The "chance it runs" percentage

- **100%**: you meet every minimum requirement and ProtonDB rates the game Platinum.
- **0%**: there's a hard blocker:
  - the GPU or CPU is below 35% of the minimum
  - RAM or video memory is below 50% of the minimum
  - a non-64-bit system
  - ProtonDB rates the game *Borked* and it has no native Linux version
- **In between**: each soft shortfall lowers the chance according to how far below the minimum you are. At the
  hard-blocker edge the maximum reduction is 85% for the GPU, 75% for the CPU, 60% for RAM and 50% for video
  memory. Other factors:
  - an SSD is required but the game would be on a hard drive: −15%
  - ProtonDB Gold: −5%, Silver: −15%, Bronze: −35%, no reports: −5%

ProtonDB's recent trend is used when it's worse than the overall rating, since recent changes such as new
anti-cheat matter most. **Disk space is reported separately** and doesn't affect the percentage, because it
stops you installing the game, not running it. The **confidence** level tells you how much of the result was
estimated or couldn't be verified.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The tests use saved Steam requirement data for 14 games (`tests/fixtures/`) and need no network access.
