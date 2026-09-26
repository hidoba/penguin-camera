"""Input locations for the two layouts of this project.

Release repository: <root>/assets/...   Development tree: <root>/analysis/...
Only data inputs of the build differ; code and outputs are identical.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = (ROOT/'assets').is_dir()
STOCK_FIRMWARE = ROOT/'flash_zb25vq32_read1.bin'


def asset(release_path, development_path):
    return ROOT/(release_path if RELEASE else development_path)


ARTWORK = asset('assets/splash-screens', 'analysis/release_artwork_05')
FRAMES = ROOT/'frames'          # update 36: every picture here becomes a camera frame
PENGUINS = ROOT/'penguins'      # update 37: every picture here is a Random-penguin print
PRINT_PENGUINS = asset('assets/print-penguins', 'analysis/penguin_print_assets_02')
MENU_PENGUIN = asset('assets/menu-penguin/penguin_96x167_16gray.png', 'analysis/menu_penguin_01/penguin_96x167_16gray.png')
GRAY_MODEL = asset('assets/gray_model.json', 'analysis/gray_model_02.json')
BOOT_ROM = asset('assets/rom/soc_boot_rom.bin', 'analysis/usb_rom_read_01/rom-read-1.bin')
