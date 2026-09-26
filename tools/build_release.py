#!/usr/bin/env python3
"""Build a release firmware image from source, reproducibly.

    python3 tools/build_release.py --version 1.0 --output build

Steps (all offline, no USB):
  1. build the relocatable payload + penguin pack from tools/ and assets/
     (build_persistent_integration with the release feature set); every
     picture in frames/ (or --frames DIR) becomes a camera frame, in file-name
     order, and only those frames are offered by up/down (1..17 pictures);
     every picture in penguins/ (or --penguins DIR) is a Random-penguin print
     (1..16 pictures),
  2. assemble the 4 MiB image on top of the stock firmware dump,
  3. emulate the SoC boot ROM's header/CRC check on the new image (it must be
     accepted, and a CRC-required control image must be rejected),
  4. write penguin-camera-<version>.bin, its .sha256 and release-manifest.json.
The same sources always give the same image (the SHA-256 is printed).
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_paths import ROOT, STOCK_FIRMWARE, ARTWORK, PRINT_PENGUINS, BOOT_ROM, FRAMES, PENGUINS

FLAGS = dict(usb_recovery=True, ram_autostart=True, penguin_corrections=True, regression_fixes=True,
             ui_cleanup=True, random_timing=True, preview_speed=True, effects_v5=True)


def sha(data): return hashlib.sha256(data).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--version', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--frames', type=Path, default=FRAMES, help='folder of frame pictures (default: frames/)')
    p.add_argument('--penguins', type=Path, default=PENGUINS, help='folder of Random-penguin pictures (default: penguins/)')
    args = p.parse_args()
    if args.output.exists(): p.error('output directory exists')
    from build_persistent_integration import assemble as build_bundle
    from assemble_persistent_candidate import assemble as assemble_image
    from trace_boot_header import trace
    from persistent_payload import unpack
    from persistent_penguins import SIZE
    stock = STOCK_FIRMWARE.read_bytes()
    bundle = args.output/'bundle'
    args.output.mkdir(parents=True)
    build_bundle(ARTWORK, bundle, PRINT_PENGUINS, frames=args.frames, penguins=args.penguins, **FLAGS)
    image, report = assemble_image(stock, stock, bundle)
    rom = BOOT_ROM.read_bytes()
    control = bytearray(stock); control[0x39] |= 128
    boot = dict(stock=trace(stock, rom)['parser_accepted'], image=trace(image, rom)['parser_accepted'],
                crc_required_control=trace(bytes(control), rom)['parser_accepted'])
    if not (boot['stock'] and boot['image']) or boot['crc_required_control']:
        raise SystemExit(f'boot header check failed: {boot}')
    payload = (bundle/'native-effects-menu.pgfx').read_bytes()
    _, relocs, hooks = unpack(payload, expected_size=SIZE)
    name = f'penguin-camera-{args.version}.bin'
    (args.output/name).write_bytes(image)
    (args.output/(name+'.sha256')).write_text(f'{sha(image)}  {name}\n')
    bundle_manifest = json.loads((bundle/'manifest.json').read_text())
    frames = [dict(frame=c['frame'], source=c['source'], resource=c['resource'], bytes=c['payload_bytes'],
                   slot_bytes=c['slot_bytes'], jpeg_quality=c['frame_audit']['quality'],
                   transparency=c['frame_audit']['transparency'])
              for c in sorted(bundle_manifest['resource_changes'], key=lambda c: c.get('frame', 0)) if 'frame' in c]
    manifest = dict(version=args.version, image=name, image_sha256=sha(image), stock_firmware_sha256=sha(stock),
                    frames=frames,
                    penguins=[dict(number=r['id'], source=r['source'], bytes=r['bytes'], jpeg_quality=r['quality'],
                                   rotated_for_roll=r['rotated_for_roll'])
                              for r in json.loads((bundle/'penguin-assets-manifest.json').read_text())['penguins']],
                    payload_sha256=sha(payload), payload_bytes=len(payload), hooks=len(hooks), relocations=len(relocs),
                    changed_sectors_vs_stock=len(report['changed_sectors']), boot_header_check=boot)
    (args.output/'release-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__': main()
