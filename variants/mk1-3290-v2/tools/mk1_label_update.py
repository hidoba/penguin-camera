#!/usr/bin/env python3
"""Exact installed build_03 -> build_04 transparent-label update: one flash sector.
Requires explicit approval. Saves a full live backup, checks it against the verified
installed image, independently checks the sector, journals erase/program, verifies
the sector twice and the full flash. Never touches boot/settings/other resources.
"""
import argparse,json,time
from pathlib import Path
import build_mk1_effects as b
from mk1_application_flash import fast_snapshot
from mk1_usb_flash import Session,CHUNK,SECTOR,SETTINGS,STOCK_SHA,BEFORE,AFTER,sha,save,journal
from mk1_fast_flash import Operations

OLD_SHA='441c5c67249f03e33bb4b25ef22ae9a0d27c9bae097e65bc91fb5ed1e6b618ba'
NEW_SHA='21d7cd67c810c993d82e11b452d3d864d0202e45a0889e0bcfa8e581dce0d1b2'
OFFSET=0x7b094;SECTOR_OFFSET=0x7b000

def plan():
    old=(b.ROOT/'analysis/build_03/image.bin').read_bytes();new,m=b.build()
    if sha(old)!=OLD_SHA or sha(new)!=NEW_SHA:raise RuntimeError('exact label transition hash mismatch')
    if new!=(b.ROOT/'analysis/build_04/image.bin').read_bytes() or m!=json.loads((b.ROOT/'analysis/build_04/manifest.json').read_text()):
        raise RuntimeError('saved candidate differs from reproducible build')
    if len(old)!=0x400000 or len(new)!=len(old) or old[:OFFSET]!=new[:OFFSET] or old[OFFSET+1:]!=new[OFFSET+1:]:
        raise RuntimeError('change extends beyond reviewed label byte')
    if (old[OFFSET],new[OFFSET])!=(250,249):raise RuntimeError('unexpected label palette change')
    previous=b.ROOT/'analysis/effects_install_activate_01'
    report=json.loads((previous/'report.json').read_text());readback=(previous/'full-readback.bin').read_bytes()
    if not report.get('completed') or report['candidate_sha256']!=OLD_SHA or sha(readback)!=report['readback_sha256']:
        raise RuntimeError('verified previous installation missing')
    masked=bytearray(readback);masked[SETTINGS:SETTINGS+SECTOR]=old[SETTINGS:SETTINGS+SECTOR]
    if masked!=old:raise RuntimeError('previous readback differs outside settings')
    logs=[json.loads(v) for v in (previous/'journal.jsonl').read_text().splitlines()]
    if logs[-1]['event']!='ram_cleanup_verified':raise RuntimeError('previous installation cleanup incomplete')
    return old,new

def install(args):
    if not args.approved_change:raise RuntimeError('explicit user approval is required')
    old,new=plan();stock=(b.ROOT/'flash_read1.bin').read_bytes()
    if sha(stock)!=STOCK_SHA:raise RuntimeError('original dump hash mismatch')
    args.output.mkdir(parents=True,exist_ok=False);s=Session(args.output,stock)
    report=dict(completed=False,flash_written=False,candidate_sha256=NEW_SHA,
        previous_candidate_sha256=OLD_SHA,sectors=[SECTOR_OFFSET],changed_byte=OFFSET,
        reference_installation='analysis/effects_install_activate_01',SD_absent_confirmed_by_user=args.sd_absent)
    started=time.monotonic();mutated=False
    try:
        s.open();s.write_packet=512;readbuf=s.allocate(CHUNK+128);source=s.allocate(SECTOR+128)
        ops=Operations(s,readbuf,source,[SECTOR_OFFSET])
        first=fast_snapshot(ops,args.output,'before-full.bin',0)
        masked=bytearray(first);masked[SETTINGS:SETTINGS+SECTOR]=old[SETTINGS:SETTINGS+SECTOR]
        if masked!=old:raise RuntimeError('live image differs outside settings; no flash writes')
        expected=bytearray(new);expected[SETTINGS:SETTINGS+SECTOR]=first[SETTINGS:SETTINGS+SECTOR]
        save(args.output/'target-live-settings.bin',expected)
        report['backup_sha256']=sha(first)
        off=SECTOR_OFFSET;before=first[off:off+SECTOR];after=bytes(expected[off:off+SECTOR])
        for poison in (0x71,0xb6):
            if ops.read(off,SECTOR,poison)!=before:raise RuntimeError('independent prewrite sector mismatch')
        raw=BEFORE+after+AFTER;s.write(source,raw,packet_size=512)
        for name in ('erase','program'):ops.refuse(name)
        if s.read(source,len(raw))!=raw:raise RuntimeError('source mismatch before erase')
        s.check();journal(args.output,'user_approved_sector',address=off,candidate_sha256=NEW_SHA)
        journal(args.output,'sector_intent',address=off,before_sha256=sha(before),after_sha256=sha(after))
        journal(args.output,'erase_intent',address=off);mutated=True;report['flash_written']=True
        status,seconds=ops.write('erase',off);journal(args.output,'erase_return',address=off,status=status,seconds=seconds)
        if status or ops.read(off,SECTOR,0x26)!=bytes([255])*SECTOR:raise RuntimeError('erase verification failed')
        if s.read(source,len(raw))!=raw:raise RuntimeError('source mismatch before program')
        s.check();journal(args.output,'program_intent',address=off)
        status,seconds=ops.write('program',off);journal(args.output,'program_return',address=off,status=status,seconds=seconds)
        if status or s.read(source,len(raw))!=raw:raise RuntimeError('program/source check failed')
        for poison in (0x36,0xc9):
            if ops.read(off,SECTOR,poison)!=after:raise RuntimeError('sector readback mismatch')
        journal(args.output,'sector_verified',address=off)
        print('Transparent label sector verified; reading back complete flash.',flush=True)
        final=fast_snapshot(ops,args.output,'full-readback.bin',2)
        if final!=expected:raise RuntimeError('full image readback mismatch')
        report.update(completed=True,readback_sha256=sha(final),settings_preserved=True,boot_preserved=True,
            all_other_bytes_preserved=final[:OFFSET]==first[:OFFSET] and final[OFFSET+1:]==first[OFFSET+1:])
        journal(args.output,'whole_image_verified',sha256=sha(final))
    except Exception as exc:
        report['error']=str(exc)
        if mutated:s.certain=False
        journal(args.output,'stopped',error=str(exc));raise
    finally:
        try:s.close()
        finally:
            report['elapsed_seconds']=round(time.monotonic()-started,3)
            (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['plan','install'])
    p.add_argument('--output',type=Path);p.add_argument('--approved-change',action='store_true');p.add_argument('--sd-absent',action='store_true')
    a=p.parse_args()
    if a.command=='plan':
        old,new=plan();print(json.dumps(dict(source_sha256=sha(old),target_sha256=sha(new),changed_sector=hex(SECTOR_OFFSET),
            changed_byte=hex(OFFSET),style='White text; transparent background; original upper information bar preserved'),indent=2));return
    if not a.output:p.error('install requires --output')
    install(a)
if __name__=='__main__':main()
