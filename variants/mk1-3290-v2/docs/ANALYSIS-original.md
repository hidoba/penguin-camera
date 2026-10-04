> Historical offline notes from before USB testing. Read [FIRMWARE.md](FIRMWARE.md) and [HISTORY.md](HISTORY.md) for later results; the final unverified section and early USB-ID assumptions below are superseded.

# MK1-3290-V2 camera — firmware analysis (offline, 2026-10-03)

Dump: `flash_read1.bin` == `flash_read2.bin`, SHA-256
`33ac2db5716dacf06b60f97c5efbb2b2b77d820fe2b13e58c1eeb4379094fab5`, JEDEC `5e 40 16`
(Zbit ZB25VQ32, 4 MiB). Read with a Pico, chip off-board.

## SoC

- Self-test string table: `chip:` **AX3291A**, `2023/12/13 V2.0`, `329X_V1.0.0`, `JRX_20220309`.
- Same family as the penguin camera's AX3295B (Appotech/BuildWin "AX329x"): 32-bit
  little-endian OpenRISC **without delay slots**, identical boot ROM header format
  (`BLDR`, load address 0x02000000; the boot stub's executable words match).
- RAM: the startup BSS clear and heap run to **0x027fec00 → 8 MiB** at 0x02000000
  (penguin: 2 MiB, 0x021fec00).
- LCD 320×240 (updater progress UI). Penguin tools: [penguin camera](../../../README.md).

## Flash layout

| Flash | Contents |
| --- | --- |
| `0x000000..0x002600` | Boot header/stub (header: start sector 0x13, 0x60d sectors) |
| `0x002600..0x0c4000` | Application (774 KiB; penguin 518 KiB) |
| `0x0c4000..0x2fe000` | Resources/data |
| `0x2fe000..0x400000` | Erased, 1032 KiB free |

`application RAM = flash offset + 0x01ffda00` (penguin: `+ 0x01ffdc00`).

## USB (same SDK as penguin)

Normal USB VID:PID **1908:3283** (and 3282), mass-storage interface **4**, EP 0x01/0x81:
same IDs as the penguin camera, so its udev rule applies unchanged.

The SCSI dispatcher (RAM 0x0204af68) is the penguin one: opcodes 0x00/0x1b/0x1e/0x2f/
0x03/0x12/0x1a/0x23/0x25/0x28/0x2a plus vendor **0xCD** (memory/call), **0xDA**
(DA/ROM-bootloader entry, RAM 0x0204adac), 0xCB/0xF0. The penguin's 0xCB/0xF1 is absent.

| Role | Flash | RAM | Penguin RAM |
| --- | --- | --- | --- |
| Memory read | 0x4cedc | 0x0204a8dc | 0x02040fa4 |
| Memory write (+callback) | 0x4cf54 | 0x0204a954 | 0x0204101c |
| **CDB wrapper (use in 0xCD CDB)** | 0x4cfd4 | **0x0204a9d4** | 0x0204109c |
| Arbitrary-function path | 0x4d010 | 0x0204aa10 | 0x020410d8 |
| DA / ROM entry (0xDA) | 0x4d3ac | 0x0204adac | 0x02041474 |
| Dispatcher | 0x4d568 | 0x0204af68 | 0x02041604 |

The memory helpers are instruction-identical to the penguin's (only the
data-structure address 0x020d31b4 differs).

## Stock helpers

| Role | Flash | RAM | ABI |
| --- | --- | --- | --- |
| Read JEDEC | 0x3eb08 | 0x0203c508 | |
| Write enable (06) | 0x3eb68 | 0x0203c568 | |
| SPI read (DMA) | 0x3ec98 | 0x0203c698 | r3 flash, r4 dst, r5 len |
| Page program (02) | 0x3ed60 | 0x0203c760 | r3 addr, r4 src |
| Multi-page program | 0x3edf4 | 0x0203c7f4 | r3 addr, r4 src, r5 len |
| 4 KiB sector erase | 0x3ee90 | 0x0203c890 | r3 addr |
| I-cache maintenance | 0x2c978 | 0x0202a378 | |
| D-cache flush | 0x2c9bc | 0x0202a3bc | r3 addr, r4 len |
| D-cache invalidate | 0x2ca38 | 0x0202a438 | r3 addr, r4 len |
| malloc | 0x3fed0 | 0x0203d8d0 | r3 size, r4 align → r11 |
| free | 0x3ffa0 | 0x0203d9a0 | r3 ptr |
| Heap list head | | 0x020c7cb4 | |

The page-program routine bit-bangs bytes instead of using DMA, and it has no
write guards. Those were penguin-project additions.

## SD-card updater (stock)

`DestBin.bin` updater at RAM 0x020024b0, called from startup (0x02000530) and
0x02002900. If an SD card is present, it opens `DestBin.bin`, erases every 4 KiB sector
the file covers, and programs everything after the first 512 bytes. It then writes the
first 512 bytes (the header) last, reads the image back to verify it, and shows
"upgrade failed!!!" on error. Other filenames referenced: `SELFTEST.bin`, `exmend.bin`.

## Tools

- `dump_flash_rp2040.py`: Pico reader (penguin header-prefix check removed).
- `tools/or1k_dis.py`: minimal no-delay OpenRISC disassembler (`or1k_dis.py file off count bias`).
- `tools/sigmatch.py`: finds penguin functions in this dump by masked signatures.
- `tools/usb_probe.py`: **read-only** USB check of the 0xCD path, to run once the
  chip is back in the camera.

## Not yet verified on hardware

Everything above is static analysis. Still to test: 0xCD RAM read/write/call on
this unit, 0xDA ROM entry and the ROM's USB PID, ROM-level flash access, and whether
the camera has an SD slot.
