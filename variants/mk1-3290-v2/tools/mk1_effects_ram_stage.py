#!/usr/bin/env python3
"""Publish one exact candidate to running application RAM for physical testing.
Flash is never written. A power cycle returns to the installed build_01 firmware.
Requires ejected media and the exact stock application preimages in idle USB mode.
"""
import argparse,json,struct,time
from pathlib import Path
import build_mk1_effects as b
from mk1_usb_flash import Session,CHUNK,SECTOR,SETTINGS,STOCK_SHA,sha,save,journal,ICACHE
from mk1_fast_flash import Operations
from mk1_application_flash import fast_snapshot,plan

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--candidate',type=Path,required=True); p.add_argument('--rehearsal',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--camera-on-disconnect',action='store_true',help='RAM-only: normal USB cleanup followed by camera mode instead of shutdown')
    p.add_argument('--reuse-backup',type=Path,help='Use an earlier verified paired backup for this RAM-only test; never valid for flash installation')
    a=p.parse_args()
    stock,base,image,m,phases=plan(a.candidate)
    tested=json.loads((a.rehearsal/'report.json').read_text())
    if not tested.get('completed') or tested['candidate_sha256']!=sha(image): raise RuntimeError('exact candidate hardware math test missing')
    a.output.mkdir(exist_ok=False,parents=True); s=Session(a.output,stock)
    result=dict(completed=False,flash_written=False,candidate_sha256=sha(image),RAM_hooks_published=False)
    published=False
    try:
        s.open(); s.write_packet=512
        g=s.read(b.G,120)
        if g[88]!=0: raise RuntimeError('photo application is active; do not replace live functions')
        before={p['offset']:s.read(p['offset']+b.BIAS,p['length']) for p in m['patches']}
        if any(v!=stock[o:o+len(v)] for o,v in before.items()): raise RuntimeError('unexpected RAM application preimage')
        for off,blob in before.items(): save(a.output/f'original-{off:06x}.bin',blob)
        save(a.output/'original-state.bin',g)
        if a.reuse_backup:
            previous=json.loads((a.reuse_backup/'report.json').read_text())
            records=[json.loads(line) for line in (a.reuse_backup/'journal.jsonl').read_text().splitlines()]
            first=(a.reuse_backup/'flash-read-1.bin').read_bytes(); second=(a.reuse_backup/'flash-read-2.bin').read_bytes()
            if not previous.get('completed') or previous.get('flash_written') or not previous.get('reads_identical'):
                raise RuntimeError('earlier RAM-only backup report invalid')
            if sha(first)!=previous['backup_sha256'] or records[-1]['event']!='ram_cleanup_verified':
                raise RuntimeError('earlier backup hash/cleanup invalid')
            elapsed=None
            result['backup_reference']=str(a.reuse_backup.resolve())
            result['backup_is_fresh']=False
        else:
            # Rehearse both fast write refusals with an impossible sector. No writer reaches SPI.
            readbuf=s.allocate(CHUNK+128); source=s.allocate(SECTOR+128); ops=Operations(s,readbuf,source,phases['payload'])
            for name in ('erase','program'): ops.refuse(name)
            started=time.monotonic(); first=fast_snapshot(ops,a.output,'flash-read-1.bin',0); second=fast_snapshot(ops,a.output,'flash-read-2.bin',1)
            elapsed=time.monotonic()-started
            result['backup_is_fresh']=True
        if first!=second: raise RuntimeError('independent readbacks differ')
        normalized=bytearray(first); normalized[SETTINGS:SETTINGS+SECTOR]=base[SETTINGS:SETTINGS+SECTOR]
        if normalized!=base: raise RuntimeError('flash differs from installed resource build outside settings')
        result.update(reads_identical=True,backup_sha256=sha(first),paired_full_backup_seconds=round(elapsed,3) if elapsed is not None else None,
            pico_differing_sectors=[hex(o) for o in range(0,len(first),4096) if first[o:o+4096]!=stock[o:o+4096]],
            fast_write_guards_refused_protected_sector=elapsed is not None)
        save(a.output/'backup.bin',first); journal(a.output,'paired_flash_backup_verified',sha256=sha(first),seconds=elapsed)
        # Disable sketch first, publish fully verified native payload, then exact hooks.
        patches=sorted(m['patches'],key=lambda p:(0 if p['offset']==0x23d98 else 1 if p['offset']==b.CAVE else 2,p['offset']))
        for patch in patches:
            off=patch['offset']; n=patch['length']; old=before[off]; addr=off+b.BIAS
            if s.read(addr,n)!=old: raise RuntimeError('RAM preimage changed immediately before patch')
            s.allowed.append((addr,addr+n)); journal(a.output,'RAM_patch_intent',address=addr,length=n)
            published=True; s.write(addr,image[off:off+n],packet_size=512); s.call(ICACHE)
            journal(a.output,'RAM_patch_verified',address=addr,length=n)
        if a.camera_on_disconnect:
            # The stock event-2 handler only reaches this instruction after G+3==0.
            # SDK registration at flash 0x2630 identifies photo mode 3. The state
            # machine still invokes the complete USB exit callback before photo init.
            addr=b.BIAS+0xf500; old=struct.pack('<I',0x9c600001); new=struct.pack('<I',0x9c600003)
            if s.read(addr,4)!=old: raise RuntimeError('unexpected disconnect-handler preimage')
            save(a.output/'original-disconnect.bin',old); s.allowed.append((addr,addr+4))
            journal(a.output,'RAM_disconnect_override_intent',address=addr,old_mode=1,new_mode=3)
            s.write(addr,new); s.call(ICACHE)
            result['RAM_only_camera_on_disconnect']=True
            journal(a.output,'RAM_disconnect_override_verified',address=addr)
        # State is initialized in the replaced table; only photo entry may claim a raw snapshot.
        result.update(completed=True,RAM_hooks_published=True,scope='Temporary application RAM; no printer invocation, reset or flash mutation')
        journal(a.output,'RAM_candidate_published',candidate_sha256=sha(image))
    except Exception as exc:
        result['error']=str(exc)
        if published: result['power_cycle_restores_installed_firmware']=True
        journal(a.output,'stopped',error=str(exc)); raise
    finally:
        try:s.close()
        finally:(a.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
