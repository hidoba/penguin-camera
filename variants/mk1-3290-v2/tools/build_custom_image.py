#!/usr/bin/env python3
"""Build custom artwork from immutable stock; retain the exact shipped app. Offline only."""
import argparse,json,subprocess,sys
from pathlib import Path
from build_variant import ROOT,FINAL_SHA
from mk1_usb_flash import SIZE,STOCK_SHA,SETTINGS,SECTOR,sha

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('content',type=Path,help='converted NNN.jpg / BMP / WAV files; complete replacement set')
    p.add_argument('output',type=Path,help='new directory')
    p.add_argument('--stock-app',action='store_true',help='graphics-only firmware retaining the stock application')
    a=p.parse_args()
    stock=(ROOT/'flash_read1.bin').read_bytes()
    if len(stock)!=SIZE or sha(stock)!=STOCK_SHA:raise ValueError('immutable stock dump changed')
    template=stock if a.stock_app else (ROOT/'analysis/build_04/image.bin').read_bytes()
    if not a.stock_app and sha(template)!=FINAL_SHA:raise ValueError('run build_variant.py; exact variant template required')
    subprocess.run([sys.executable,str(ROOT/'tools/build_image.py'),str(a.content.resolve()),str(a.output.resolve())],check=True)
    path=a.output/'image.bin';image=bytearray(path.read_bytes());image[0x2600:0xc4000]=template[0x2600:0xc4000]
    if image[:0x2600]!=stock[:0x2600] or image[SETTINGS:SETTINGS+SECTOR]!=stock[SETTINGS:SETTINGS+SECTOR]:
        raise ValueError('protected bytes changed')
    path.write_bytes(image)
    mp=a.output/'manifest.json';m=json.loads(mp.read_text());m.update(image_sha256=sha(image),application_sha256=sha(image[0x2600:0xc4000]),application='stock' if a.stock_app else 'build_04',
        changed_sectors=[hex(o) for o in range(0,SIZE,SECTOR) if image[o:o+SECTOR]!=stock[o:o+SECTOR]])
    mp.write_text(json.dumps(m,indent=2)+'\n');print('Custom image SHA-256:',sha(image))
if __name__=='__main__':main()
