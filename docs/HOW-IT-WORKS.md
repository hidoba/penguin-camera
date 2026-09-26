# How it works

How this firmware changes the camera. For the stock firmware itself (memory map,
addresses, stock functions, events) see [STOCK-FIRMWARE.md](STOCK-FIRMWARE.md).

## Hardware

| Part | Detail |
| --- | --- |
| Board | AMG-05-3295B-V1 |
| SoC | AX3295B family, OpenRISC 1000 core (no branch delay slots) |
| Flash | Zbit ZB25VQ32, 4 MiB SPI NOR, JEDEC `5e 40 16` |
| Printer | LIYIN MTP02-IXC thermal head, 384 dots per line |
| Screen | 320×240 (ST7789V) |
| Stock firmware | `20250102 V2.6` |

The boot ROM loads the application (flash `0x2400..0x83e00`) to RAM at
`0x02000000`; a flash offset *o* is RAM address `o + 0x01ffdc00`.

## Flash layout of the release image

| Flash | Contents |
| --- | --- |
| `0x000000..0x003000` | Boot header and stub — never changed |
| `0x019000` | Boot loader, written over the stock SD-card updater (which is disabled) |
| `0x039000` | Stock flash-write guards: only the settings sector may be erased/programmed at runtime; block/chip erase denied |
| stock resources | Your frames (`frames/`) in the largest of the 17 frame slots, power-on/off splash screens from `assets/splash-screens/` (same slot sizes); internal-photo writers disabled (print-only camera) |
| `0x1d7000` | Your settings (preserved) |
| `0x1d8000..0x200000` | Internal-photo area (preserved, unused) |
| `0x200000` | Payload `PGFX`: relocatable code + data, relocation and hook tables, CRC |
| `0x240000` | Penguin pack (PGPK): the `penguins/` pictures as print-layout JPEGs (`tools/penguins.py`) for *Random penguin* |

## Boot

At start-up the loader allocates `0x30400` bytes on the heap, reads the payload
from flash, checks its header and CRC, applies the relocation records for the
actual address, verifies that every stock word it is about to patch still has its
expected original value, and only then writes the RAM hooks (38, plus up to 12
for the frames; the loader allows 64). If any check
fails nothing is patched and the camera runs stock.

## Hooks (RAM patches of the stock application)

| Area | What changes |
| --- | --- |
| Photo path | preview frame submission → our preview pipeline; capture forced to memory, always printed; JPEG workspace loan (101 KB, reuses preview banks); print preprocessing → our print transform |
| Frames | frame index → resource table entries moved to the chosen slots; the last-frame limits (stock: 17) set to the number of pictures in `frames/` (`tools/frames.py`) |
| Buttons | camera-mode event table remapped (switch camera / menu / dither toggle); up/down cycle none ↔ frames only, no zoom; key-dispatcher trampoline: auto-off reset on any press, OK cycles the curve, menu shutter/4th button |
| Menu | native 3-item menu (display, previous/next/activate), opens on Camera; Random-penguin worker with 1.5 s debounce |
| Screen | overlay flip hook paints the curve/dither names, clock and pictograms; stock clock widget hidden |
| Date | stock imprint disabled; our stamp follows the stock setting |
| Sensor | stock colour effects disabled (curves replace colour filters) |
| Start-up | cold boot goes straight to Camera |

## Preview (every camera frame, 320×240 YUV 4:2:0)

1. Orange-filter gray: `Y + 0.4855 (Cr−128) − 0.0491 (Cb−128)`.
2. Tone curve (AUTO LEVELS: 1/128 tails clipped, ≥ 64-level range; or a LUT).
   With a stock frame active, the frame image (luma > 27 = frame) masks its
   pixels out of the AUTO histogram; prints with a frame reuse that
   photo-only table.
3. Dithering off: neutral chroma. Dithering on: the selected kernel (diffusion
   kernels are sliced over several frames to keep the preview responsive).
4. The overlay (names, clock, icons) lives on the stock UI layer, never in the photo.

Halftones: a cosine screen `cos(2π/p·x)·cos(2π/p·y)` of period *p* compared
with the picture blurred by one screen cell (Gaussian sigma = cell / 6, from
`gaussf(img, s)` against a screen of period `6s`): `[1 4 6 4 1] / 16`
(sigma 1.0) for 6×6, `[1 7 16 7 1] / 32` (sigma 0.83) for 5×5. Less blur tears
the dots and gives moiré; more blur loses edges the dot grid could still show.

## Print (384 dots × feed)

Same gray and curve as the preview, then either the dithering kernel or the
measured tone model (`tools/tone_gray.py`, model `assets/gray_model.json`):
each dot's printer input is shifted for the line load, heat history and head
position measured with a spectrophotometer. The date stamp is added last.

## Payload memory map

See `tools/payload_layout.py` for the runtime (writable) regions; everything
else is checked byte for byte by `penguin_flash.py check`.

| Offset | Contents |
| --- | --- |
| `00000`–`01100` | Print entry and transform, shutter/capture wrappers, fallback LUT |
| `01100`–`04200` | Sliced diffusion kernels (+ menu penguin bitmap in their free tails) |
| `04200`–`04f00` | Preview hook, tables, controls, runtime state |
| `05000`–`0bf00` | Print kernels, Halftone 5×5 (`05400`), curve LUTs/code, icons, Halftone 6×6, date stamp, orange conversion, tone model |
| `0c400`–`0ee40` | Screen overlay, preview worker, label font and names |
| `0f000`–`27c00` | Preview banks + work image (runtime) |
| `27c00`–`2b800` | Magic 4×4 (not selectable) / Error Diffusion 1D kernels, print scratch (runtime) |
| `2b800`–`2e000` | Native menu and capture loan |
| `2e000`–`30400` | Flash-backed Random-penguin reader/worker, tables |

## How the code is written

There is no C compiler for this target in the loop: `tools/` contains Python
generators that emit OpenRISC machine code (`or1k_subset.Assembler`) together
with a Python reference implementation of every routine. The tests run the
generated code in a small OpenRISC emulator (`or1k_subset.CPU`) and require it to
match the reference bit for bit.

## Installer

`tools/penguin_flash.py` talks to the stock firmware's USB mode (vendor SCSI
command `0xCD`: read RAM, write RAM, call). It runs the firmware's own SPI read
routine to dump the flash and a generated, sector-bound RAM bridge (it can erase
and program exactly one 4 KiB sector, from one buffer) to write. See the
[README](../README.md#safety) for the safety rules.
