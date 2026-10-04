# Firmware reference for the exact MK1 stock image

Board **MK1-3290-V2**, chip **AX3291A**, flash **Zbit ZB25VQ32**, JEDEC
`5e 40 16`, 4 MiB flash, 8 MiB RAM, 320×240 LCD. Stock strings include
`2023/12/13 V2.0`, `329X_V1.0.0`, `JRX_20220309`. CPU is 32-bit little-endian
OpenRISC without delay slots. These are findings for our dump, not a guarantee
that every similarly branded toy has the same firmware.

## Flash and RAM

| Flash interval (end exclusive) | Contents |
| --- | --- |
| `0x000000..0x002600` | Boot header and initialization stub: preserve all bytes |
| `0x002600..0x0c4000` | Application, loaded to RAM at `0x02000000` |
| `0x0c4000..0x2fd000` | Stock resources/data; directory at `0x0d1200` |
| `0x2fd000..0x2fe000` | Persistent settings: preserve the full live 4 KiB sector |
| `0x2fe000..0x400000` | Erased stock tail, used for relocated replacement resources |

`application RAM address = flash offset + 0x01ffda00`.
Penguin bias is `0x01ffdc00`: do not reuse its addresses.
Boot header is `BLDR`, application starts at sector `0x13` and has `0x60d`
512-byte sectors. Resource directory base is `u32(header+0x18) × 512`.
Each entry has little-endian `(relative_offset, byte_length)`; resource address
is directory base + relative offset. The first directory word gives its table
size (`word / 8` entries). Resource IDs are zero-based numbered entries.

The stock loaders use the table's offset/length and allocate length bytes;
relocation need not stay below the settings sector. Static analysis located
persistent writes to the settings sector and the `DestBin.bin` SD updater; it
motivated use of the erased tail. This is a bounded analysis finding, not a claim
about every unknown SDK path or future firmware version.

Replacement payloads align to `0x100` in the tail. Small replacements fit their
original slot; shorter files leave old tail bytes unused, with the directory's
size shortened. Build from immutable stock and a complete replacement set each
time; the populated tail of an installed custom image is not an erased allocation
base. At installation, compare sectors against the actual previous full image.
Publish the resource-directory sector `0xd1000` last.

## Resource formats and IDs

| ID | Role | Required stored format |
| --- | --- | --- |
| 1–10 | Photo frames (custom selection uses 1–7) | 1280×720 baseline JPEG 4:2:0 |
| 11 | Menu background | 320×240 baseline JPEG |
| 32, 34–38 | Six menu icons/buttons | 96×96 bottom-up 24-bit BMP, 27,704 bytes |
| 33 | Composite main menu | 320×240 baseline JPEG |
| 48, 49 | Power-on and power-off screens | 320×240 baseline JPEG |

Frame transparency is **decoded Y≤26**, not JPEG alpha. prepare_new_content.py
uses PNG alpha≥128 to mark artwork; all other pixels become black. Artwork Y is
lifted by `40 + Y×215/255`, then weak pixels/neighbours are raised in a closed-loop
encode/decode pass. Reported artwork min-Y should exceed 30 and background leaks
should be zero. Inspect preview masks after every re-encode/quality adjustment.

Menu BMP key is RGB `(140,140,140)`. Exact header/size is retained, including two
trailing bytes; inside artwork matching the key is nudged off it. For menu 33,
86×86 buttons go at: 35 `(17,22)`, 38 `(117,22)`, 32 `(218,22)`, 36 `(17,131)`,
34 `(117,131)`, 37 `(218,131)`. Their graphics change; their stock menu actions do
not. Stock sounds/game/text resources are not deliberately replaced by our art.

To inspect all original entries without hardware:

```bash
python3 tools/extract_resources.py resources
```

This creates ignored `resources/` with raw numbered entries, a manifest,
decoded previews and a contact sheet. `tools/or1k_dis.py` can inspect instructions
using the MK1 bias. The original reverse-engineering notes are in
[ANALYSIS-original.md](ANALYSIS-original.md); their final “not yet verified” section
and speculative USB identifiers are historical, superseded by later evidence.

## USB and flash helpers

Our stock unit: SD inserted → storage `0219:3280`; no SD → webcam `1908:3282`.
MK1 probe/session also recognizes `1908:3283` but still checks the actual interface
and helper code. Storage interface **4**, bulk OUT **0x01**, IN **0x81**, class 8,
subclass 6, protocol 80. SCSI vendor CDB **0xCD** provides memory/callback access
through wrapper **0x0204a9d4**. Stock 0xDA ROM-entry exists but was not exercised;
no ROM-entry/reset is needed by this workflow.

| Helper | Flash offset | RAM address | Main arguments |
| --- | --- | --- | --- |
| SPI read | `0x3ec98` | `0x0203c698` | r3 flash, r4 destination, r5 length |
| Page program | `0x3ed60` | `0x0203c760` | r3 flash, r4 source |
| Multi-page program | `0x3edf4` | `0x0203c7f4` | r3 flash, r4 source, r5 length |
| 4 KiB erase | `0x3ee90` | `0x0203c890` | r3 flash |
| D-cache flush | `0x2c9bc` | `0x0202a3bc` | r3 address, r4 length |
| D-cache invalidate | `0x2ca38` | `0x0202a438` | r3 address, r4 length |
| I-cache maintenance | `0x2c978` | `0x0202a378` | stock ABI |
| malloc | `0x3fed0` | `0x0203d8d0` | r3 size, r4 alignment; r11 result |
| free | `0x3ffa0` | `0x0203d9a0` | r3 pointer |

Heap list head `0x020c7cb4`; nodes `0x020c7cbc..0x020c80bc`; USB session allocations
must lie in `0x020f0000..0x027fec00`. The USB display buffer is verified against the
heap and descriptor before its final 512 bytes and result scratch are temporarily
borrowed, saved and restored. Larger scratch allocations are explicitly owned.

Stock SPI program routines have no safety guards. Host/session checks and small
native guards supply them: exact whitelisted sector, exact source/length, request
seal, independent source/read buffers, canaries, cache maintenance, verified RAM
uploads, and completion marker `0x1970`. Resident application guards only accept
application sectors by default; the reusable graphics updater explicitly requests
resource-only scope and refreshes exact guards per sector. Neither can reach the
boot sector or settings. DMA reads poison their destination and check guards to
avoid accepting stale RAM as newly read flash.

USB mode patch at flash `0x4b318`: instruction `0xa46300ff` (`andi r3,r3,0xff`)
becomes `0x9c600000` (`addi r3,r0,0`). This forces selector 0, the stock storage
mode, regardless of original selector 0/1/2. It does not implement a second USB
protocol. Offline native tests passed; permanent no-card enumeration still needs
the cold-start reports described in WORKFLOW.md.

## Effects implementation

The code lives in the retired sketch lookup-table area
`0x785d8..0x7c3dc`: **15,876 bytes available, 14,432 bytes used**. It is already
loaded with the stock application, so no replacement boot loader/header is needed.
The builder validates stock preimages, code bounds and layout convergence; exact
patch/symbol addresses are emitted in `analysis/build_04/manifest.json`.

- UI interceptors clear old colour/sketch/kaleidoscope selection state, offer
  seven frames and three effects, and preserve printing on/off selection.
- Penguin YUV grayscale uses Q8 Cr/Cb weights KR=124, KB=13, roughly
  0.55R + 0.40G + 0.05B, with chroma neutralized after conversion.
- Bayer uses Penguin Auto Levels; halftone and Floyd–Steinberg use the Penguin
  More Contrast LUT. Auto Levels sampling excludes a selected frame's transparent
  opening rules where relevant; thresholds and minimum sample/range rules come
  from the ported implementation.
- Halftone 6×6 uses the port's cell-scale prefilter and threshold matrix.
  Floyd–Steinberg uses bounded integer diffusion scratch.
- Printer adapter applies the Penguin measured `input_shift` model with head
  position, line-load and history compensation. This model was measured on the
  penguin, **not newly calibrated on the MK1 printer**. Original motor, battery,
  heating and thermal guard routines remain.
- Date stamp uses current RTC and original date-print enable setting, black
  `YYYY-MM-DD` with a 2-pixel white outline, bottom-left margin 10. Stock capture
  date overlays are bypassed to avoid adding a second/date-style stamp.
- Printing+dither capture retains an owned raw 1280×720 YUV snapshot. Saved JPEG
  receives its own dithering; the printer resamples raw capture to 384-dot output
  before dithering, avoiding a second dither of JPEG dots. If raw allocation fails,
  diagnostic state word +28 becomes 1 and the saved photo stays raw; printer can
  process decoded JPEG. This memory fallback is a real limitation.
- Screen labels use 2× 5×7 glyphs, white palette index **251**, transparent **249**.
  Owned area X=0..223,Y=32..63; glyph origin `(4,40)`. Rows 0..31 of the original
  information bar are untouched. Labels do not enter capture/printer pixels.

Useful globals (exact firmware only):

| RAM | Role |
| --- | --- |
| `0x020cee44` | Photo state: +88 active, +89 category, +90 colour, +91 special, +93 kaleidoscope, +94 frame, +95 print, +109 countdown |
| `0x020c7c08` | OSD: +8 geometry, +16/+24 buffers, +20/+28 lengths (76,800 bytes) |
| `0x020c870c` | Capture: +12/+14 width/height; +40 bank 2 selects +24/+28 YUV, else +32/+36 |
| `0x020c7c74` | RTC date |
| `0x020c1ab8` | Original date-print setting (`0x81000015` enables it) |

Capture state +2 is a frame-composition flag, not the date setting. Preserve it.
No-delay CPU emulation checks native output, register/stack behaviour, bounds,
canaries, selection and OSD preservation; private RAM hardware rehearsals compare
native pixels with independent Python reference output. Neither substitutes for
physical saved-photo, print or cold-boot testing.
