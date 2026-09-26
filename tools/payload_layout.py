"""Payload regions written at runtime (offsets from the relocated payload base).

Everything else in the 0x30400-byte payload is static and must equal the build
byte for byte after loading (checked by firmware_boot_check.py). Keep this list
in sync with the modules that own the state (update 32 review).
"""
from persistent_penguins import SIZE

RUNTIME = (
    (0x0f00, 0x0f20, 'print diagnostics (photo_print_policy)'),
    (0x4e00, 0x4f00, 'preview STATE, PRINT_STATE, sliced META, capture loan, curve state'),
    (0x7900, 0x8000, 'curve-icon LUT copy, preview/print auto-levels scratch'),
    (0x8000, 0x8f00, 'preview scratch 320x12'),
    (0xf000, 0x27c00, 'preview banks + WORK_IMAGE (also the photo JPEG loan)'),
    (0x28800, 0x2b800, 'print scratch 1024x12'),
    (0x2d600, 0x2d610, 'menu STATUS'),
    (0x2fc00, 0x2fc40, 'penguin worker STATE'),
    (0x30200, 0x30210, 'flash worker CONTROL'),
)
PENGUIN_TABLE = 0x2fd00        # 16-byte records; the first word of each is a runtime pointer
PENGUIN_TABLE_EXT = 0x2ec00    # update 38: the same records for 17..64 photos (flash_penguin_worker.layout)


def runtime_mask(penguin_count=16):
    mask = bytearray(SIZE)
    for lo, hi, _ in RUNTIME: mask[lo:hi] = b'\1'*(hi-lo)
    for i in range(penguin_count): mask[PENGUIN_TABLE+16*i:PENGUIN_TABLE+16*i+4] = b'\1'*4
    for i in range(64): mask[PENGUIN_TABLE_EXT+16*i:PENGUIN_TABLE_EXT+16*i+4] = b'\1'*4   # zero when unused
    return bytes(mask)
