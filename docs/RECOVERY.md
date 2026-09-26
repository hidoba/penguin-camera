# Recovery with a Raspberry Pi Pico (last resort)

Use this only if the camera no longer starts and USB installation is impossible.
You need a Raspberry Pi Pico with MicroPython, a 3.3 V supply, fine soldering or
a SOIC-8 clip, and `pip install pyserial`.

**Status:** reading the flash this way is proven (the stock dump in this
repository was made with it). **Restoring has not been tested on real hardware.**

## Isolate the flash chip first

The ZB25VQ32 must not be driven by the camera's SoC while the Pico talks to it.
Disconnecting only CS is not enough, and an unpowered board can be back-powered
through the pins. Lift the chip (or otherwise isolate its bus) before connecting.
The stock dump in this repository was read with the chip desoldered: with the
board powered, the AX3295B starts reading the flash itself at the same time as
the Pico.

## Wiring (Pico SPI0, mode 0, 2 MHz)

| Pico pin | Flash SOIC-8 pin |
| --- | --- |
| 21 / GP16 (MISO) | 2 / DO |
| 22 / GP17 (CS) | 1 / CS# |
| 23 / GND | 4 / GND |
| 24 / GP18 (SCK) | 6 / CLK |
| 25 / GP19 (MOSI) | 5 / DI |
| external 3.3 V (shared ground) | 8 / VCC; tie 3 / WP# and 7 / HOLD# high |

## Read (always twice, and compare)

```bash
python3 tools/recovery/dump_flash_rp2040.py /dev/ttyACM0 read1.bin
python3 tools/recovery/dump_flash_rp2040.py /dev/ttyACM0 read2.bin
cmp read1.bin read2.bin
```

## Restore

`tools/recovery/restore_flash_rp2040.py` accepts the stock firmware
(`flash_zb25vq32_read1.bin`) or your own complete backup (`before.bin` /
`backup.bin` from `penguin_flash.py`). It reads the chip twice, journals every
step, rewrites only the sectors that differ, and verifies the whole chip.
Run it with `--help` for the exact arguments.
