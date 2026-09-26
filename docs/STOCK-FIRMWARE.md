# Stock firmware reference

This is a reverse-engineering reference for the **stock** firmware of the camera
(`20250102 V2.6`), as found in the flash dump shipped with this repository
(`flash_zb25vq32_read1.bin`, SHA-256
`e22557a4497a1199c9ecc3956b18a89ac70af800b674055513f3de91bfb8f224`).
It describes what the vendor firmware does and where. How this project patches
it is described separately in [HOW-IT-WORKS.md](HOW-IT-WORKS.md); reading and
restoring the flash chip with a Raspberry Pi Pico is in [RECOVERY.md](RECOVERY.md).

All names below are ours. There are no debug symbols; a function name means
"what the code was observed to do", not a recovered identifier. Statements
marked *(unverified)* or *hypothesis* have not been confirmed on hardware or by
a complete trace.

## Contents

- [Hardware](#hardware)
- [Address conventions](#address-conventions)
- [Flash layout](#flash-layout)
- [Boot process](#boot-process)
- [Resources](#resources)
- [RAM map and important globals](#ram-map-and-important-globals)
- [Stock functions](#stock-functions)
- [Modes, events and buttons](#modes-events-and-buttons)
- [Display](#display)
- [Camera preview and frame compositing](#camera-preview-and-frame-compositing)
- [Frames](#frames)
- [Capture and printing](#capture-and-printing)
- [Settings storage](#settings-storage)
- [USB](#usb)
- [Flash writers and write protection](#flash-writers-and-write-protection)
- [Tools for exploring](#tools-for-exploring)
- [Open questions](#open-questions)

## Hardware

| Part | Detail |
| --- | --- |
| Board | AMG-05-3295B-V1 |
| SoC | AX3295B family (firmware string `Ax3295B-V3.0` at flash `0x1bae0d`) |
| CPU | OpenRISC 1000, 32-bit, little-endian instruction words, **no branch delay slots** |
| Boot ROM | 32 KiB at `0x00100000..0x00108000`; contains `BuildWinVideo050Loader  1.00.1.00 Thunder` at `0x00105e10` |
| Flash | Zbit ZB25VQ32, 32 Mbit / 4 MiB SPI NOR, JEDEC ID `5e 40 16` |
| Printer | LIYIN MTP02-IXC thermal print head, 384 dots per line |
| Screen | 320×240, ST7789V controller (string `st7789v` at flash `0x83e38`) |
| USB | VID:PID `1908:3283` in normal firmware, `1908:3319` in the ROM loader |
| Firmware | `20250102 V2.6` (string at flash `0x6c05c`, RAM `0x02069c5c`) |

Notes:

- The image sensor has not been identified on this board. A similar camera uses
  a GC2A06, but that is not proof for this one.
- Printing needs the battery. On USB power alone the motor can run without
  advancing the paper even though the firmware reports success.
- Because there are no delay slots, disassemblers that assume the standard
  OpenRISC delay slot produce wrong control flow. GNU `or1k-elf-objdump`
  decodes the instructions correctly if every 32-bit word is byte-reversed first
  and the file is disassembled as big-endian binary; treat its branch
  formatting as cosmetic.

## Address conventions

The boot ROM copies the application from flash `0x2400..0x83e00` (0x81a00
bytes) to RAM at `0x02000000`. For anything inside the application:

```text
RAM address = flash offset + 0x01ffdc00        (0x01ffdc00 = 0x02000000 - 0x2400)
```

The tools call this constant `BIAS`. Cross-checks: the version string reference
at flash `0x8318` resolves to RAM `0x02069c5c`, and the print code's `4.0`
constant at RAM `0x0206c744` is found at flash `0x6eb44`.

Rules of thumb used throughout this document:

- **Flash** means a byte offset in the 4 MiB image. **RAM** means a CPU
  address. Tables give both where the location is part of the application.
- The mapping does **not** apply to the boot header, the boot stub, the
  resources or anything else outside `0x2400..0x83e00`.
- RAM `0x02000000..0x02081a00` is the loaded image. Globals above
  `0x02081a00` (for example `0x0208xxxx`) are zero-initialised data with no
  flash image. The heap lives at roughly `0x02090000..0x02200000`.

## Flash layout

| Flash range | Contents |
| --- | --- |
| `0x000000..0x000200` | Boot header (512 bytes), parsed by the boot ROM |
| `0x000200..0x002400` | Boot stub, 8704 bytes, loaded to SRAM address `0` |
| `0x002400..0x083e00` | Application code and initialised data, loaded to RAM `0x02000000` |
| `0x083e00..0x085200` | Data outside the loaded application (the `st7789v` string at `0x83e38` is here); not analysed |
| `0x085200..0x085450` | Resource directory (74 entries × 8 bytes) |
| `0x085450..0x1d6abb` | 74 contiguous resource payloads |
| `0x1d6abb..0x1d7000` | Declared resource area ends at `0x1d6c00`; the bytes up to `0x1d7000` are not all `ff` *(purpose unknown)* |
| `0x1d7000..0x1d8000` | Settings sector (see [Settings storage](#settings-storage)) |
| `0x1d8000..0x200000` | Internal-photo storage area (metadata starts at `0x1d8000`) |
| `0x200000..0x400000` | Erased (`ff`) in the stock image |

In the stock dump the last non-`ff` byte is at `0x1d8fff`: the internal-photo
area holds one sector of metadata and no photos. The settings sector address is
exactly the resource end rounded up to 4 KiB.

Other landmarks inside the application:

| Flash | RAM | What |
| --- | --- | --- |
| `0x19000` (sector) | | Sector containing the stock SD-card firmware updater |
| `0x1996c..0x19d48` | `0x0201756c` | SD-card updater function (see below) |
| `0x39000` (sector) | | SPI flash driver and internal-photo writers |
| `0x6c05c` | `0x02069c5c` | Version string `20250102 V2.6` |
| `0x6c194` | `0x02069d94` | Literal `DestBin.bin` |
| `0x6dc24` | `0x0206b824` | USB device descriptor, `1908:3283` |
| `0x6dc38` | `0x0206b838` | Alternate USB device descriptor, `1908:3282` |
| `0x6dc4c` | `0x0206b84c` | Initial USB device descriptor, `0219:3280` |

## Boot process

### Boot header

The first 512 bytes are a header read by the boot ROM. The fields we know:

| Header offset | Stock value | Meaning |
| --- | --- | --- |
| `0x04` | `"BLDR"` | Magic *(inferred)* |
| `0x0a` (byte) | `0x05` | Flags; bit `0x04` makes the ROM skip the 512-byte additive header checksum |
| `0x18` | `0x429` | Resource directory, in 512-byte sectors (`0x429 × 512 = 0x85200`) |
| `0x1c` | `0xa8d` | Resource area length in sectors (ends at `0x1d6c00`) |
| `0x20` | `0x02000000` | Application load address |
| `0x24` | `0x12` | Application start, in sectors (`0x2400`) |
| `0x28` | `0x40d` | Application length, in sectors (`0x81a00` bytes) |
| `0x38` | `0x00000800` | Copied into the ROM's SPI flag byte (see below) |

The other words (for example `0x10..0x17` = ASCII `01234567` and
`0x30`/`0x34` = `0x01234567`) have not been interpreted. Application code at
flash `0x4dad4` reads the resource fields, which confirms their meaning.

### What the ROM does

Traced offline by running the captured ROM's header parser (ROM `0x100478`)
in an emulator, with SPI reads and special registers modelled:

1. Read the header from flash `0`. Header byte `0x0a` bit `0x04` is set, so the
   additive header checksum at ROM `0x100588..0x100594` is skipped.
2. The SPI flag byte (ROM state `0x4f89`) starts as `0x80`; ROM
   `0x100644..0x100648` loads header word `0x38` into state `0x4f88`, turning
   the flags into `0x08`.
3. Load the 8704-byte boot stub from flash `0x200` to address `0` and the
   530,944-byte application from flash `0x2400` to `0x02000000`.
4. ROM `0x1006e8..0x100708` compares a CRC of the SPI data but **rejects a
   mismatch only when the flag byte is negative** (bit 7 set). With the stock
   `0x08` the CRC result is ignored. Setting bit 7 of header byte `0x39`
   makes the same image fail, which the trace uses as a negative control.
5. Jump to the entry (reached at ROM `0x100728`).

Consequences: the stock header does not make the ROM enforce an image
checksum, so the application and resources can be changed without updating
header checksums. This is a static/emulated result, not a cold-boot test: the
boot stub's calibration call at `0x1198` and the final hardware and cache
cleanup were stubbed, and the application itself was not executed.

### Application start-up

The application start-up routine at flash `0x2870` (RAM `0x02000470`):

- calls the SD-card updater `0x1996c` at flash `0x2890` (RAM `0x02000490`);
- requests the initial mode with `l.addi r3,r0,6` at flash `0x28ac`
  (RAM `0x020004ac`): the stock camera boots into the **main menu** (mode 6);
- writes runtime settings bytes at `0x2780`, `0x2794` and `0x278c`
  (the same bytes are rewritten when settings are refreshed, at `0x7dc8`,
  `0x7d38`, `0x7de0`).

The updater also has a second, runtime caller at flash `0x19d54`.

### SD-card updater (`DestBin.bin`)

The updater at flash `0x1996c` (RAM `0x0201756c`) mounts the card and looks for
`DestBin.bin`. Observed flow:

1. Get the file length; allocate a 4096-byte buffer; show progress.
2. Try a **whole-chip erase** at `0x19b2c`; on failure, erase 4 KiB sectors.
3. Program everything after the first 512 bytes at matching flash offsets.
4. Program the first 512 bytes (the boot header) last.
5. Read back in 2048-byte chunks and compare with the file.

No signature or header check before the erase was found on this path (that
does not rule out checks elsewhere). Treat it as a raw full-image flasher: a
wrong file or a power failure during the update leaves an unbootable camera.

## Resources

### Directory format

The directory is at `header[0x18] × 512` = flash `0x85200`. It is an array of
little-endian `<offset u32, size u32>` pairs. Offsets are relative to the
directory start. The first offset is `0x250`, so the directory holds
`0x250 / 8 = 74` entries (resource IDs 0..73). Payloads are contiguous: each
entry's offset equals the previous offset plus size, and the last payload ends
at `0x1d6abb`. The global at RAM `0x0208abcc` holds `0x85200` (the directory
base).

Contents: **31 JPEG, 15 BMP, 8 WAV, 20 other** binary entries. All JPEG and BMP
images decode.

### Resource list

| ID | Flash offset | Size (bytes) | Content |
| --- | --- | --- | --- |
| 0 | `0x085450` | 11,880 | Binary, starts with `XDBB` *(unidentified)* |
| 1–17 | `0x0882b8`–`0x0ea34d` | 12,559–37,384 | Photo frames, 640×360 baseline JPEG (table below) |
| 18 | `0x0f11b7` | 51,785 | Fish background, 320×240 JPEG |
| 19 | `0x0fdc00` | 22,595 | Flower/navigation background, 320×240 JPEG |
| 20 | | 86,400 | Binary *(unidentified)* |
| 21–24, 34 | | 3,132–7,035 | 320×240 JPEGs |
| 26, 29, 41–46 | | | WAV sounds |
| 36, 37 | `0x12a7d9`, `0x12ba0f` | 4,662 | 48×32 BMP |
| 38 | `0x12cc45` | 82,944 | Six 96×96 menu icons (camera, video, playback, settings, games, music), 13,824 bytes each, Y plane + interleaved VU |
| 39 | `0x141045` | 32,911 | Six-icon main menu, 320×240 JPEG |
| 47–59 | | 440 each | 8×16 BMP (glyph-sized) |
| 62 | `0x1885c6` | 1,024 | UI overlay palette: 256 entries, RGB565 + alpha byte *(alpha interpretation from use)* |
| 64 | `0x188dc6` | 32,103 | Goodbye (power-off) screen, 320×240 JPEG |
| 65 | `0x190b2d` | 28,433 | Hello (power-on) screen, 320×240 JPEG |
| 68 | `0x1b863c` | 10,193 | 320×240 JPEG |
| 69 | `0x1bae0d` | 12 | ASCII `Ax3295B-V3.0` |
| 71 | `0x1d029d` | 13,934 | USB screen, 320×240 JPEG |
| 72 | `0x1d390b` | 8,120 | Camera screen, 320×240 JPEG |
| 73 | `0x1d58c3` | 4,600 | Battery screen, 320×240 JPEG |

The remaining binary entries (25, 27, 28, 30–33, 35, 40, 60, 61, 63, 66, 67, 70)
are not decoded; fonts and other UI data are likely among them. Resource 38's
YCbCr range and the meaning of its gray corners are inferred from appearance.

Frame slots (all 640×360 baseline JPEG):

| ID | Flash offset | Slot bytes | | ID | Flash offset | Slot bytes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `0x0882b8` | 15,517 | | 10 | `0x0c238d` | 23,007 |
| 2 | `0x08bf55` | 19,142 | | 11 | `0x0c7d6c` | 22,231 |
| 3 | `0x090a1b` | 33,090 | | 12 | `0x0cd443` | 21,988 |
| 4 | `0x098b5d` | 32,308 | | 13 | `0x0d2a27` | 12,559 |
| 5 | `0x0a0991` | 22,577 | | 14 | `0x0d5b36` | 25,669 |
| 6 | `0x0a61c2` | 19,170 | | 15 | `0x0dbf7b` | 31,600 |
| 7 | `0x0aaca4` | 37,384 | | 16 | `0x0e3aeb` | 26,722 |
| 8 | `0x0b3eac` | 30,968 | | 17 | `0x0ea34d` | 28,266 |
| 9 | `0x0bb7a4` | 27,625 | | | | |

### Replacing a resource in place

Because the directory stores exact sizes and payloads are contiguous, the
simplest safe change keeps every slot where it is. A replacement JPEG must be
baseline (not progressive), have the same dimensions, components and chroma
subsampling, and be no larger than the slot; the rest of the slot is padded
with `ff` after the EOI marker. BMP replacements must keep the header, palette
and length. Relocating resources or changing the directory has not been
explored. `tools/replace_resource.py` does the in-place variant.

## RAM map and important globals

All of these are in the zero-initialised area above the loaded image, so they
have no flash offset.

| RAM address | Type | Meaning |
| --- | --- | --- |
| `0x02081ac0` | u32[] | Settings table (entry *n* at `+4n`); source of the first settings page |
| `0x02081b04` | u32 | Settings entry 17: date imprint, `0x81000015` = on, `0x81000014` = off |
| `0x02081bc0` | | Source of the second settings page |
| `0x02085d04` | table | Active global event table (188 bytes), built by `0x865c` from flash table `0x804d0` |
| `0x02085dc8` | table | Active per-mode event table, filled by `0x85a4` from the current screen |
| `0x02085e9c` | u32 | Active mode: 1 power off, 3 camera, 6 main menu, 8 settings, 9 USB |
| `0x02085ea4` | | Mode registry |
| `0x02085f34` | u32 | Auto-power-off idle counter (ms) |
| `0x0208671c` | u32 | Uptime in milliseconds, advances in steps of 10, wraps |
| `0x020867a0` | ptr | Retained camera frame descriptor (set by `0x2233c`, read by `0x2235c`) |
| `0x02086928` | u32 | LCD start state |
| `0x02086970` | u8 | LCD enabled |
| `0x02086971` | u8 | LCD pool selector |
| `0x02086988` | ptr | Current LCD descriptor |
| `0x020869a0` | struct | UI overlay layer descriptor (see [Display](#display)) |
| `0x02086a00` | struct | Real-time clock copy; `+12` u16 year, then u8 month, day, hour, minute, second (so the year is at `0x02086a0c`) |
| `0x02087c04` | ptr | Heap list head |
| `0x02087c0c..0x0208800c` | 16-byte nodes | Heap records `{used, pointer, size, next}`; `(1,0,0,0)` at `0x02087c0c` is the sentinel |
| `0x02088414` | u32 | Printer busy (non-zero while printing) |
| `0x020892c8` | struct | Camera state / runtime settings block, called **G** below |
| `0x0208935c` | struct | Capture globals |
| `0x02089374`, `0x02089378` | ptr | Capture raw Y / chroma buffers |
| `0x02089394`, `0x02089398` | ptr, u32 | JPEG workspace pointer and capacity |
| `0x0208a1dc`, `0x0208a2d4` | u32 | Capture width / height (observed 640, 480) |
| `0x0208a640`, `0x0208a674` | 52 bytes | Camera frame descriptors (320×240, double-buffered) |
| `0x0208a6e4`, `0x0208a718` | 52 bytes | LCD descriptors (240×320, double-buffered) |
| `0x0208a974` | struct | Frame compositor state: `+0` active, `+28`/`+30` u16 width/height (320×240), `+36` pointer to the frame's luma |
| `0x0208abcc` | u32 | Resource directory base, `0x85200` |

Camera state block **G** = `0x020892c8`:

| Offset | Address | Meaning |
| --- | --- | --- |
| +1 | `0x020892c9` | Power state (5 on USB, 6 on battery observed) |
| +3 | `0x020892cb` | Start-up source (2 when started by USB) |
| +97 | `0x02089329` | Effect category: 0 none, 1 colour filter, 2 kaleidoscope, 3 frame |
| +98 | `0x0208932a` | Colour filter 1..5 |
| +99 | `0x0208932b` | Zoom |
| +100 | `0x0208932c` | Retained preview frame claim state (the photo loop sets it to 2 after submitting) |
| +101 | `0x0208932d` | Kaleidoscope index |
| +102 | `0x0208932e` | Frame index 0..16 |
| +108 | `0x02089334` | Main menu selection |
| +122 | `0x02089342` | Print/save toggle (1 = print the photo) |
| +123 | `0x02089343` | Print rendering selection |
| +124 | `0x02089344` | Print density 0..3 |

Initialised data inside the image:

| Flash | RAM | Meaning |
| --- | --- | --- |
| `0x804d0` | `0x0207e0d0` | Global event table (source of `0x02085d04`) |
| `0x805d8` | `0x0207e1d8` | Camera-mode event table |
| `0x8065c` | `0x0207e25c` | Frame index → resource ID table, 17 × u32 |
| `0x826b8` | `0x020802b8` | Main-menu event table |

## Stock functions

Calling convention: arguments in `r3..r8`, return value in `r11`, stack
pointer `r1`, link register `r9`. No delay slots. Callers are expected to check
return codes.

### Memory, cache and time

| Flash | RAM | Purpose / arguments |
| --- | --- | --- |
| `0x3ace0` | `0x020388e0` | Heap allocate: `r3` size, `r4` alignment → `r11` pointer |
| `0x3adb0` | `0x020389b0` | Heap free: `r3` pointer |
| `0x29790` | `0x02027390` | Data-cache flush (write back): `r3` address, `r4` length |
| `0x2980c` | `0x0202740c` | Data-cache invalidate: `r3` address, `r4` length |
| `0x2974c` | `0x0202734c` | Instruction-cache maintenance |
| `0x29ca0` | `0x020278a0` | Asynchronous DMA memory copy |
| `0x29c3c` | `0x0202783c` | Wait for / retire the `0x29ca0` copy |
| `0x2144c` | `0x0201f04c` | Read uptime (delay helper at `0x2146c`) |

### SPI flash

| Flash | RAM | Purpose / arguments |
| --- | --- | --- |
| `0x39398` | `0x02036f98` | Read JEDEC ID → `r11` (`0x5e4016`) |
| `0x393f8` | `0x02036ff8` | Write enable |
| `0x3942c` | `0x0203702c` | Wait while busy |
| `0x39528` | `0x02037128` | Read: `r3` flash offset, `r4` RAM destination, `r5` length; uses DMA (lengths are rounded to 16 bytes, so flush/invalidate around it) |
| `0x395f0` | `0x020371f0` | Page program (256 bytes): `r3` flash address, `r4` source |
| `0x39670` | `0x02037270` | Multi-page program: `r3` address, `r4` source, `r5` length |
| `0x3970c` | `0x0203730c` | 4 KiB sector erase (`0x20`): `r3` sector address |
| `0x39778` | `0x02037378` | Block erase |
| `0x397dc` | `0x020373dc` | Chip erase (`0xc7`) |
| `0x39838` | `0x02037438` | Internal-photo write |
| `0x39a6c` | `0x0203766c` | Internal-photo delete |
| `0x39c04` | `0x02037804` | Internal-photo format |
| `0x2c438`, `0x2c48c`, `0x2c4e0`, `0x2c650` | `0x0202a038`, `0x0202a08c`, `0x0202a0e0`, `0x0202a250` | Low-level SPI byte out, byte in, buffer transfer, chip select |

### Modes, UI and display

| Flash | RAM | Purpose / arguments |
| --- | --- | --- |
| `0x908c` | `0x02006c8c` | Request mode: `r3` mode number (dispatcher at `0x9120`) |
| `0x7bac` | `0x020057ac` | Read settings entry `r3`, mapped to a small value (e.g. 1/0) |
| `0x865c` | `0x0200625c` | Build the active global event table |
| `0x85a4` | `0x020061a4` | Copy a screen's event handlers into the active table |
| `0x86d8..0x8830` | `0x020062d8..` | Queued-key dispatcher |
| `0xf270` | `0x0200ce70` | Auto power-off: `r3 != 0` resets the idle counter, `r3 == 0` is the periodic tick (called via `0xf978`) |
| `0x609c` | `0x02003c9c` | Claim a display descriptor from pool `r3` (0 = photo/menu pool) |
| `0x6058` | `0x02003c58` | Submit a claimed descriptor |
| `0x408f4` | `0x0203e4f4` | Release a claimed descriptor |
| `0x405dc` | `0x0203e1dc` | Set viewport |
| `0x3064c` | `0x0202e24c` | Start the image engine (sets bit 1 of SPR `0xc800`) |
| `0x32b80` | `0x02030780` | Start the LCD transfer (sets bit 1 of SPR `0xa800`) |
| `0x35d04` | `0x02033904` | Display driver: programs the image engine from the current descriptor |
| `0x368fc` | `0x020344fc` | LCD enable: `r3` enable flag |
| `0x3783c` | `0x0203543c` | Overlay flip: `r3` layer, `r4` buffer to show (called at `0x61d0` in `0x619c`) |

### Camera, image and print

| Flash | RAM | Purpose / arguments |
| --- | --- | --- |
| `0x940c` | `0x0200700c` | Camera preview loop (per frame) |
| `0x361c8` | `0x02033dc8` | Submit a preview frame for display |
| `0x2233c` / `0x2235c` | `0x0201ff3c` / `0x0201ff5c` | Store / return the retained frame descriptor |
| `0x40b74` | `0x0203e774` | Load and show a frame resource: `r3` resource ID |
| `0x40f58` | `0x0203eb58` | Frame compositor (per-pixel loop `0x40fcc..0x41104`) |
| `0x4a3e4` | `0x02047fe4` | Sensor colour effect: writes sensor registers `0xBA`/`0xBB` |
| `0x9860` | `0x02007460` | Capture routine |
| `0x9bd8` | `0x020077d8` | Photo-to-print capture |
| `0x3cb24` | `0x0203a724` | Allocate capture raw and JPEG buffers |
| `0x3cc80` | `0x0203a880` | Free capture raw buffer |
| `0x3cd64` | `0x0203a964` | Capture cleanup |
| `0x54ebc` | `0x02052abc` | Date imprint renderer |
| `0x4ba2c` | `0x0204962c` | JPEG decode / resize from memory (384-dot print width) |
| `0x4d718` | `0x0204b318` | Print a JPEG file |
| `0x4d8d8` | `0x0204b4d8` | Print a JPEG in memory |
| `0x4d498` | `0x0204b098` | Mode-1 preprocessing, then binary print |
| `0x2254c` | `0x0202014c` | Local contrast enhancement (CLAHE-like) |
| `0x22d20` | `0x02020920` | Error diffusion (Stucki-like), in place |
| `0x4c834` | `0x0204a434` | Multilevel raster output |
| `0x4d0ac` | `0x0204acac` | Binary raster output (threshold 128) |
| `0x4cf20` | `0x0204ab20` | Guarded grayscale print wrapper (see [Capture and printing](#capture-and-printing)) |
| `0x4b918` | `0x02049518` | Print-head strobe helper (sets bit 9 of SPR `0x8000`, delays, clears it) |
| `0x4b960` | `0x02049560` | Quantises a sampled value to 0..8 *(sensor meaning unknown)* |
| `0x4bed8` | `0x02049ad8` | Printer pin setup: `r3 = 0` save/configure, `r3 = 1` restore |

### USB

| Flash | RAM | Purpose |
| --- | --- | --- |
| `0x43a04` | `0x02041604` | Vendor command dispatcher |
| `0x4349c` | `0x0204109c` | Memory transfer wrapper used by `0xCD` |
| `0x433a4` | `0x02040fa4` | Device-to-host memory read |
| `0x4341c` | `0x0204101c` | Host-to-device memory write |
| `0x434d8` | `0x020410d8` | Arbitrary-function call path |
| `0x43874` | `0x02041474` | `0xDA` handler: enter the ROM USB loader (dispatch at `0x43a9c`) |

## Modes, events and buttons

### Modes

The active mode is the u32 at `0x02085e9c`; `0x908c(mode)` requests a change.
Known values: 1 power off, 3 camera, 6 main menu, 8 settings, 9 USB. The stock
camera starts in the main menu (6). Other values (0, 2, 4, 5) appear in the
auto-off logic but have not been mapped to screens.

### Event dispatch

Buttons and firmware-internal events are queued and dispatched by
`0x86d8..0x8830`. Each event is looked up in two tables:

1. the **per-mode table** at `0x02085dc8` (copied from the current screen by
   `0x85a4`), then
2. the **global table** at `0x02085d04` (built by `0x865c` from flash `0x804d0`).

Handlers are called with `r4 = 1` and `r5` pointing to the press subtype word:
0 = fresh press, 2 = release. The dispatcher validates the event number
(≤ `0x30`); all paths converge at flash `0x87c0` (RAM `0x020063c0`) with the
event in `r2`, and the primary handler lookup is compared at `0x87d4`.

Tables are arrays of `{u32 event, u32 handler RAM address}` pairs terminated
by event `0x31` with handler 0.

### Event codes

| Event | Source | Stock meaning |
| --- | --- | --- |
| `0x09`, `0x0b` | Periodic | Frequent background events (seen continuously in USB mode) |
| `0x10` | Screen | Init |
| `0x11` | Screen | Cleanup |
| `0x1c` | Button | OK (top-left button) |
| `0x1e`, `0x20` | Buttons | Up / down (the pair on the right edge; which is which was not pinned down) |
| `0x24` | | "Previous" in the menu, same handler as `0x1e` |
| `0x26` | Button | Third left button: switch camera (front/back) in camera mode |
| `0x29` | Button | Shutter |
| `0x2a` | Queued | Capture, queued after the shutter |
| `0x2b`, `0x2c`, `0x2d` | | Present in the global table; not identified |
| `0x2e` | Button | Second left button: print/save toggle |
| `0x30` | Button | Fourth left button: back / menu. Also **posted by firmware** on dialog exit (`0x1a1d0`, `0x1c308`) and by USB code (`0x20bc4`) |

The physical positions come from on-device key traces and the button layout
(four buttons on the left, top to bottom `0x1c`, `0x2e`, `0x26`, `0x30`).

### Camera-mode table (flash `0x805d8`, RAM `0x0207e1d8`)

| Event | Handler (RAM) | Handler (flash) | Behaviour |
| --- | --- | --- | --- |
| `0x10` | `0x02008db4` | `0xb1b4` | Init |
| `0x11` | `0x02008790` | `0xab90` | Cleanup |
| `0x12` | `0x02008d4c` | `0xb14c` | *(unidentified)* |
| `0x29` | `0x02008fe0` | `0xb3e0` | Shutter |
| `0x2a` | `0x020095b4` | `0xb9b4` | Capture (and print) |
| `0x26` | `0x02009480` | `0xb880` | Switch camera |
| `0x1e` | `0x02009dd4` | `0xc1d4` | "Up": previous effect / frame; hold = zoom |
| `0x20` | `0x02009af4` | `0xbef4` | "Down": next effect / frame; hold = zoom |
| `0x30` | `0x020093c0` | `0xb7c0` | Back to the main menu (`0x908c(6)`) |
| `0x2e` | `0x020092cc` | `0xb6cc` | Toggle print/save (G+122, ~200-tick debounce) |
| `0x01`, `0x02`, `0x03`, `0x06`, `0x0b` | `0x02009228`, `0x020082bc`, `0x02008284`, `0x02007f9c`, `0x02008520` | `0xb628`, `0xa6bc`, `0xa684`, `0xa39c`, `0xa920` | *(unidentified)* |

OK (`0x1c`) has no camera-mode handler. A second screen with the same kind of
controls (probably playback) uses `0xde44` for `0x2e`, `0xe60c` for `0x1e`
and `0xe7e0` for `0x20` (table pointers at flash `0x80ba0`, `0x80bc8`,
`0x80bd0`).

### Global table (flash `0x804d0`, RAM `0x0207e0d0`)

Handlers for `0x1c 0x1e 0x20 0x2c 0x2d 0x24 0x26 0x30` live at
`0x8c88..0x8eb8`; they reset the auto-off idle counter on a fresh press. There
are also entries for `0x2b` (`0x8bc0`), `0x01`, `0x02`, `0x03` and `0x0b`.
The shutter (`0x29`/`0x2a`) and the print toggle (`0x2e`) have no global
handler, so in stock firmware taking photos does **not** postpone auto-off.

### Auto power-off

`0xf270` with `r3 == 0` is the periodic tick (via `0xf978`): at least every
500 ms it adds 500 to `0x02085f34` and requests mode 1 once the counter reaches
the auto-off setting (`0x7bac(9)`) × 1000 ms. The counter is held at 0 in modes
0 and 9, and while `0x47948`, `0x5755c` or `0x46008` report busy in modes 2, 5
and 4.

### Main menu (flash `0x826b8`, RAM `0x020802b8`)

| Event | Handler (flash) | Behaviour |
| --- | --- | --- |
| `0x10` | `0x17dac` | Init (resets the selection G+108; the clamp is `l.sfleui r4,N` at `0x17de8`) |
| `0x11` | `0x17ae4` | Cleanup |
| `0x1e`, `0x24` | `0x17c6c` | Previous item |
| `0x20` | `0x17be4` | Next item |
| `0x1c` | `0x17f10` | Activate the selected item (slot at RAM `0x020802e4`) |
| `0x09` | `0x17b68` | Blink |
| `0x0b` | `0x17928` | *(unidentified; the menu draw routine is at `0x17940`)* |

The stock menu has six items (camera, video, playback, settings, games, music),
drawn from the JPEG background (resource 39) and the icon strip (resource 38).
The shutter and back buttons have no menu entry.

## Display

- The LCD panel is driven in portrait: LCD descriptors (`0x0208a6e4`,
  `0x0208a718`) are 240×320 with a 256-byte stride, 122,880 bytes per buffer
  (81,920 Y + 40,960 chroma). The current one is pointed to by `0x02086988`.
- Camera/menu content is landscape: descriptors in pool 0 (`0x0208a640`,
  `0x0208a674`) are 320×240, stride 320, 115,200 bytes (76,800 Y + 38,400
  chroma, format byte 4). The hardware rotates them for the panel.
- 52-byte descriptor layout: `+4` Y pointer, `+8` chroma pointer, `+24` total
  bytes, `+32` u16 width, `+34` u16 height, `+44` u16 stride, `+46` format byte,
  `+49` owned flag, `+50` pool. `+12`/`+16` were observed as zero.
- Presenting a buffer: flush the data cache (`0x29790`), then the display
  driver (`0x35d04`) programs the image engine and starts it (`0x3064c`), and
  `0x32b80` starts the LCD transfer.
- **UI overlay layer** (status bar, icons, clock): descriptor at `0x020869a0`,
  `+8` u16 240, u16 320; `+0x10`/`+0x18` two 76,800-byte buffers; `+0x20` the
  buffer in use. It is 8-bit palettised (palette = resource 62); landscape
  screen pixel (X, Y) is byte `(319 − X) × 240 + Y`. Index 249 is transparent,
  250 the bar background, 251 white text. Rows 0..31 are the top bar.
- The overlay is re-rendered into the spare buffer and flipped about once per
  second (clock refresh) by `0x619c`, which calls the flip `0x3783c` at
  `0x61d0`. The canvas is copied into that buffer with the asynchronous DMA
  copy `0x29ca0`, and the flip does not wait for it.

Buffer addresses depend on heap allocation order; follow the descriptors rather
than hard-coding pixel addresses.

## Camera preview and frame compositing

Per preview frame the photo loop at `0x940c`:

1. claims the retained frame via G+100 and gets its descriptor with `0x2235c`;
2. optionally applies effects at `0x9478` and composites a frame at `0x948c`
   (call to `0x40f58`);
3. sets G+100 to 2 and submits the frame at `0x949c` → `0x361c8`. For format 4
   `0x361c8` gets a destination via `0x40860` and routes the source through
   `0x3fe2c`.

Preview frames are 320×240 YUV 4:2:0 and double-buffered (descriptors
`0x0208a640` / `0x0208a674`).

Effects:

- **Colour filters** (category 1, index G+98 = 1..5) are not pixel operations;
  `0x4a3e4` programs sensor registers `0xBA`/`0xBB`.
- **Kaleidoscopes** (category 2, index G+101) *(implementation not analysed)*.
- **Frames** (category 3) are composited by `0x40f58`.

Frame compositing: the compositor state at `0x0208a974` holds the decoded frame
image (`+36` luma pointer, `+28`/`+30` 320×240). In the per-pixel loop
(`0x40fcc..0x41104`) the frame's own luma is compared with 27
(`l.sfleui rX,27`): a frame pixel with **Y ≤ 27 is transparent** and lets the
photo through; brighter frame pixels replace the photo. There is no alpha
channel: dark areas of the frame artwork are the window.

## Frames

The frame selection is stored in the camera state block:

- G+97 = 3 when a frame is active, G+102 = frame index 0..16.
- The 17-word table at flash `0x8065c` (RAM `0x0207e25c`) maps the index to a
  resource ID. Stock order: `1, 2, 10, 11, 12, 13, 14, 15, 16, 17, 3, 4, 5, 6,
  7, 8, 9`.
- `0x40b74` (RAM `0x0203e774`) loads and shows a frame resource, `r3` =
  resource ID. It is called from both up/down handlers (at `0xc094` and
  `0xc39c`) and from `0x115f8`, `0x11aa8` and `0x11c74`.

Cycling. The down handler (`0x20` → flash `0xbef4`, RAM `0x02009af4`) steps
none → colour filters → kaleidoscopes → frames → none; the up handler
(`0x1e` → flash `0xc1d4`, RAM `0x02009dd4`) goes the other way. The last frame
index is hard-coded:

| Flash | RAM | Stock instruction | Role |
| --- | --- | --- | --- |
| `0xc06c` | `0x02009c6c` | `l.sfgtui r4,15` | Down: after index 15 the next step leaves frames |
| `0xc2cc` | `0x02009ecc` | `l.addi r3,r0,16` | Up from none: enter index 16 |
| `0xc2dc` | `0x02009edc` | `l.lwz r3,0x40(r3)` | Up from none: table entry 16 |
| `0x11a88` | `0x0200f688` | `l.addi r3,r0,16` | Second handler (`0x11a74`): previous wraps to 16 |
| `0x11c48` | `0x0200f848` | `l.sfgtui r3,16` | Second handler (`0x11c3c`): next wraps after 16 |

Holding up or down zooms instead (zoom branches at `0xc3f4` in the up handler
and `0xc12c` in the down handler; zoom value in G+99). Where the second
handler pair is used from has not been determined.

A frame is a 640×360 baseline JPEG in one of the 17 fixed-size slots. The
camera shows it over the 320×240 preview (and the compositor state holds it at
320×240). Artwork conventions that follow from the threshold: keep everything
meant to be visible clearly above Y = 27 after JPEG decoding (JPEG ringing can
push edge pixels across the threshold), and keep the window at pure black.

## Capture and printing

### Capture

- The shutter (`0x29` → `0xb3e0`) queues event `0x2a`, whose handler `0xb9b4`
  (RAM `0x020095b4`) captures. Depending on the print/save flag (G+122) it
  calls `0x9f70` or `0x9bd8`, then prints the JPEG from memory at `0xbbf8`
  (call to `0x4d8d8`). A separate branch calls the capture routine `0x9860`.
  Printing can therefore be preceded by encoding and storage work.
- Capture is 640×480 YUV (raw buffer 460,800 bytes) encoded to JPEG. The raw
  buffer and a separate JPEG workspace are allocated by `0x3cb24` (called at
  `0x9c80`) and released by `0x3cd64` (called at `0x9eb4`). If the workspace
  allocation fails the stock cleanup path can skip freeing the raw buffer.
- The capture path compares the JPEG size with `0x14000` bytes and re-encodes
  at lower quality above that *(from notes; threshold behaviour not traced in
  detail)*.
- The date imprint is controlled by settings entry 17 (`0x7bac(17)`), read in
  `0x9860` (at `0x98c4`) and in `0x9bd8` (at `0x9c30`), and rendered by
  `0x54ebc` (callers `0x489bc`, `0x48bbc`, `0x54504`). The capture hardware
  setup routines `0x48850`, `0x540d4`, `0x3d9e8` and `0x52954` also receive the
  flag.
- The print/save toggle G+122 is also cleared at `0xb7f4` and `0xdde4`, and
  written at start-up and on settings refresh (see [Application
  start-up](#application-start-up)).

### Print pipeline

`0x4d8d8` (memory) and `0x4d718` (file) decode the JPEG with `0x4ba2c`, scaled
to the 384-dot width, then either preprocess (mode 1, `0x4d498`) and use the
binary driver, or use the multilevel driver through the wrapper `0x4cf20`.

Mode-1 preprocessing:

- **Local contrast enhancement** at `0x2254c` (called at `0x4d6c8`): 64-bin
  histograms and a clip limit of `4.0`; its structure and error codes closely
  match Zuiderveld's CLAHE (an algorithm identification, not a claim of
  identical code).
- **Error diffusion** at `0x22d20` (called at `0x4d6e8`): threshold 128,
  integer error weights with denominators 42 and 21, resembling Stucki. In an
  isolated emulation of a 16×8 image it leaves row 0 unchanged and writes into
  the following image plane (an emulation observation only).

Raster output:

- `0x4c834` builds thresholds `0, 8, …, 248` (multilevel gray output). `0x4d0ac` prints binary (threshold 128).
- Both count the active dots per line and adjust the strobe time (capped at
  8000 internal units, not calibrated to microseconds) and step the motor
  inside the pass loop. The strobe helper `0x4b918` pulses bit 9 of SPR
  `0x8000`.

Grayscale print wrapper `0x4cf20` (RAM `0x0204ab20`):

| Register | Value |
| --- | --- |
| `r3` | Y plane (one byte per dot) |
| `r4` | Feed length in lines |
| `r5` | 384 |
| `r6` | Density 0..3 (G+124) |
| `r7` | Mode (0 = gray without preprocessing) |
| `r8` | Power state (G+1) |

Bracket it with `0x4bed8(0)` and `0x4bed8(1)`. The data is stored rotated:
logical pixel (x, y) of the 384-dot-wide strip is at `data[(383 − x) × feed + y]`.
While printing, `0x02088414` is non-zero.

Measured on this camera at density 3 through the multilevel driver: input 0
and 33 are visibly different, and inputs above about 189 print as white.

## Settings storage

- The settings live in flash sector `0x1d7000`. The first page is written from
  RAM `0x02081ac0`, the second (`0x1d7100`) from `0x02081bc0`.
- The table is an array of u32 entries; many values have the form
  `0x81xxxxxx`, and `0x7bac(n)` maps entry *n* to the value the UI uses.
  Entry 9 is the auto-off time, entry 17 the date imprint (`0x81000015` on,
  `0x81000014` off). Other entries are not mapped.
- The settings writer lives near flash `0x5408` *(from a code note; not
  traced)*. Changing a setting erases and rewrites this sector.
- The real-time clock value is kept in RAM at `0x02086a00` (year at `+12`) and
  survives power-off (battery-backed).

## USB

### Normal USB mode

Selecting USB on the camera (mode 9) enumerates as `1908:3283`, product
`GENERAL - AUDIO`, with video interfaces 0/1 and a USB mass-storage interface
4 (class 8, subclass 6, protocol `0x50`, bulk OUT `0x01`, bulk IN `0x81`).

Besides normal SCSI commands, the stock firmware accepts a **vendor SCSI
command `0xCD`** inside a Bulk-Only Transport command block:

```text
CDB (16 bytes) = cd | handler u32 | address u32 | callback u32 | 00 00 00
                    (little-endian)
handler  = 0x0204109c  (memory transfer wrapper)
callback = 0xffffffff for none, otherwise a RAM function address
```

- With data-in, the wrapper reads `length` bytes from `address`; with data-out
  it writes them. The direction comes from the CBW flags (`0x80` = to host).
- If `callback` is set, the firmware calls it before the data phase finishes.
  This is how the tools in this repository call stock functions: upload a small
  stub, then trigger it as a callback. The tools use the data-cache flush
  `0x29790` as callback on every write and call `0x2974c` after uploading code.
- A wrapper returns a 13-byte CSW (`USBS`, same tag, residue 0, status 0).

Example: reading the version string (16 bytes at `0x02069c5c`):

```text
cd 9c 10 04 02 5c 9c 06 02 ff ff ff ff 00 00 00
```

The vendor command runs arbitrary code with no authentication. It is
available while the camera is in USB mode (mode 9, USB screen shown).

### ROM USB loader

A second vendor command, `0xDA` (handled at `0x43874`, RAM `0x02041474`),
makes the normal firmware jump into the SoC's boot ROM loader. The camera
disconnects and enumerates as `1908:3319` with mass storage on interface 0,
bulk OUT `0x01` / IN `0x81`. (A related camera reports this loader as
`BLDR v1.00` / `BuildWin Video050Loader 1.00`.)

- The ROM loader has its own vendor command `0xCB` with a memory helper at
  ROM `0x00102620` (not interchangeable with the normal `0xCD` handler).
  Memory reads through it were bounded at 256 bytes in tests.
- Small code can run in ROM SRAM at `0x4800..0x4c00`. Other SRAM observations:
  callback stack around `0x4f14`, ROM USB state at `0x4f74`, USB buffers near
  `0x4440` and `0x8000`. Do not assume `0x5000..0x7fff` is free.
- The ROM SPI controller is at SPRs `0x9000` (control), `0x9250` (TX), `0x9244`
  (start), `0x9248` (done), with clock gates `0x810c`/`0x804c`. Flash can be
  read, programmed and erased from there by direct SPI commands.
- An **idle watchdog** returns to the normal firmware about 2 seconds after the
  last host command. All RAM patches are lost in the process, which makes
  `0xDA` a convenient software restart.

Limits: entry into the ROM loader has only been demonstrated from a working
application. There is no known strap or button combination that enters it when
the application does not boot. On a related camera with micro-USB, grounding
the USB ID pin entered the loader; this board has USB-C, which has no ID pin.
If the application is broken, the fallback is reading/writing the flash chip
directly ([RECOVERY.md](RECOVERY.md)).

## Flash writers and write protection

The stock firmware has no flash write protection of its own. Code paths that
can modify flash:

| Writer | Flash | Scope |
| --- | --- | --- |
| SD-card updater | `0x1996c` | Whole chip (tries chip erase first) |
| Settings writer | via `0x395f0` / `0x3970c` | Sector `0x1d7000` |
| Internal-photo write / delete / format | `0x39838`, `0x39a6c`, `0x39c04` | Internal-photo area from `0x1d8000` (the format path is reachable during start-up) |
| Chip / block erase | `0x397dc`, `0x39778` | Whole chip / one block |

We found no write-protection mechanism in the stock firmware: its own
routines can erase and program any sector, and the SD-card updater rewrites
the whole chip including the boot header.

Our firmware adds entry guards on these routines (only the settings sector can
be erased/programmed at run time); see [HOW-IT-WORKS.md](HOW-IT-WORKS.md).

## Tools for exploring

All in [`tools/`](../tools):

- `analyze_firmware.py` — parses the resource directory, extracts every
  resource with a manifest, PNG previews and a contact sheet, and writes the
  word-reversed application image for a big-endian OpenRISC disassembler.
- `replace_resource.py` — in-place resource replacement with the size and
  format checks described above.
- `trace_boot_header.py` — runs the boot ROM's header parser (ROM image in
  `assets/rom/soc_boot_rom.bin`) against a flash image in an emulator; see
  [Boot process](#boot-process).
- `or1k_subset.py` — small no-delay-slot OpenRISC interpreter (`CPU`) and
  assembler (`Assembler`). It covers the instructions used by the tools; no
  peripherals, caches or interrupts.
- `camera_usb.py` — the `0xCD` USB transport: memory read/write, calling stock
  functions with arguments, heap checks, and flash reads through `0x39528`.

## Open questions

- Header words other than those listed, and the boot stub's work.
- The purpose of the non-`ff` bytes in `0x83e00..0x85200` and
  `0x1d6abb..0x1d7000`.
- The layout of the internal-photo metadata at `0x1d8000` (its first words are
  `0x001d8000`, `0x00227000`, `0x1b`); whether the internal-photo writer could
  reach beyond `0x200000` has not been ruled out.
- The 20 binary resources, including likely fonts.
- Modes 0, 2, 4, 5 and the screens behind them; events `0x01..0x0b`, `0x12`,
  `0x2b`, `0x2c`, `0x2d`.
- Kaleidoscope implementation and count.
- The image sensor and its register map beyond `0xBA`/`0xBB`.
- Strobe/heater timing in physical units.
- ROM loader entry when the application does not boot.
