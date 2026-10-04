# Script map

All tools belong to this variant and use paths relative to it. The shipped Penguin
ports are self-contained in `tools/penguin_port/`; do not add a developer's home
folder to Python imports. Install the variant requirements first.

| Script | Purpose / status |
| --- | --- |
| `build_variant.py` | Offline: exact blurred public build_01 resources plus build_04 effects, checked against published hashes |
| `extract_resources.py` | Offline: original numbered resources, manifest and previews in ignored resources/ |
| `prepare_new_content.py` | Offline: source PNG/BMP art → stored camera formats, alpha/luma checks and previews; needs NumPy |
| `build_image.py` | Offline: stock-base resource packing/relocation; reference stock hash required regardless of file name |
| `build_custom_image.py` | Offline: new resource build plus exact stock or build_04 app; does not alter native effects |
| `build_mk1_effects.py` | Offline: exact MK1 native code, hooks, selection, label/date/tone/USB patches; fixed build_01 input |
| `usb_probe.py` | Hardware read-only: vendor RAM path and stock helpers; optional full RAM dump |
| `mk1_usb_flash.py` | Hardware-tested fixed build_01 backup/three resource phases; also supplies Session transport/ownership/journal primitives |
| `mk1_application_flash.py` | Hardware-tested build_01 → exact effects payload/activation phases; requires current candidate rehearsal |
| `mk1_flash_reader.py` | Native guarded SPI read generator; no hardware action on import |
| `mk1_fast_flash.py` | Hardware-tested resident application operations; repository adds explicit resource-only scope and refresh method, covered offline |
| `mk1_artwork_flash.py` | Repository graphics-only plan/backup/verify/three-phase updater; native/simulated tests, not yet hardware-run |
| `mk1_effects_rehearsal.py` | Private owned-RAM native pixels/tone/date vs reference; no live hooks or flash writes |
| `mk1_effects_ram_stage.py` | Temporary live camera hooks for physical review; no flash; optional disconnect change is RAM-only |
| `mk1_verify_effects_boot.py` | Read-only exact-build RAM verification after a real restart; candidate argument is the build **directory** |
| `mk1_no_sd_access_check.py` | No-card native read/guard-refusal check requiring verified no-card boot evidence; still awaiting physical use |
| `trace_mk1_boot_header.py` | Offline captured boot-ROM header parser with positive/negative controls; not full boot emulation |
| `mk1_native.py`, `penguin_port/` | No-delay instruction generation/emulator and bounded Penguin algorithm ports |
| `or1k_dis.py` | Offline minimal disassembler |
| `test_mk1*.py` | Offline native emulator and simulated installer tests; never claim USB |
| `99-mk1-camera.rules` | Linux USB permission rule for 0219:3280 |

`mk1_label_ram_revision.py` and `mk1_label_update.py` are retained as **historical
transition source**, not general installers. They require old build_02/build_03
artifacts, original private build inputs/configuration and exact private-run
evidence not shipped here. Do not fabricate those
reports or change hashes to make them pass. A new initial build_04 install already
contains their final changes and uses the documented workflow instead.

Likewise, do not use an earlier rehearsal's report for a different candidate.
The historical application script has a very specific two-instruction label
revision exception; it is not permission to inherit testing for new pixels/code.
For changes beyond graphics, make a fresh native and physical RAM validation plan.

The fixed resource script also exposes a manual resume-state argument for diagnosed
partial states. It is not an automatic recovery path. The reusable artwork entry
point deliberately refuses an unknown preimage and provides no blind resume.
