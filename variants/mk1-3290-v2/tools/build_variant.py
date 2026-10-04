#!/usr/bin/env python3
"""Recreate the exact resource build_01 and transparent-label build_04 with blurred public artwork. Offline."""
import json, subprocess, sys
from pathlib import Path
import build_mk1_effects as effects
from mk1_usb_flash import STOCK_SHA, TARGET_SHA, sha
ROOT=Path(__file__).resolve().parents[1]
FINAL_SHA='c50df9e03f36f38ba19f4f909f4eb350ab0ce913dbc23ff52ca96b32bd2216c3'

def main():
    stock=(ROOT/'flash_read1.bin').read_bytes()
    if sha(stock)!=STOCK_SHA: raise ValueError('immutable stock dump changed')
    resources=ROOT/'analysis/build_01'; final=ROOT/'analysis/build_04'
    if not resources.exists():
        subprocess.run([sys.executable,str(ROOT/'tools/build_image.py'),str(ROOT/'assets/prepared'),str(resources)],check=True)
    if sha((resources/'image.bin').read_bytes())!=TARGET_SHA: raise ValueError('reference resource build changed')
    image,manifest=effects.build()
    if sha(image)!=FINAL_SHA: raise ValueError('reference effects build changed')
    if final.exists():
        if (final/'image.bin').read_bytes()!=image or json.loads((final/'manifest.json').read_text())!=manifest:
            raise ValueError('existing build_04 differs; retain and investigate')
    else:
        final.mkdir(parents=True)
        (final/'image.bin').write_bytes(image)
        (final/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Resource image:',resources/'image.bin',TARGET_SHA)
    print('Complete variant:',final/'image.bin',FINAL_SHA)
if __name__=='__main__':main()
