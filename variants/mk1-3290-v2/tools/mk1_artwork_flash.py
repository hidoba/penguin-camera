#!/usr/bin/env python3
"""Portable graphics-only updater, using the tested MK1 transport and resident helpers.
Repository adaptation: offline and simulated checks only; this entry point has not
been run on hardware. It cannot change an application, boot bytes or live settings.
Each write phase requires human approval and the exact reviewed target SHA-256.
"""
import argparse,json
from pathlib import Path
from mk1_usb_flash import (ROOT,SIZE,SECTOR,CHUNK,SETTINGS,STOCK_SHA,Session,sha,save,
                          journal,snapshot,BEFORE,AFTER)
from mk1_fast_flash import Operations
from mk1_application_flash import fast_snapshot
from build_variant import FINAL_SHA
PHASES=('relocated','inplace','directory')
DIRECTORY=0xd1000

def normalized(data,settings):
    if len(data)!=SIZE:raise ValueError('image must be 4 MiB')
    out=bytearray(data);out[SETTINGS:SETTINGS+SECTOR]=settings
    return bytes(out)

def reference():
    stock=(ROOT/'flash_read1.bin').read_bytes()
    if len(stock)!=SIZE or sha(stock)!=STOCK_SHA:raise ValueError('stock reference changed')
    return stock

def known_image(path,stock):
    image=path.read_bytes()
    if len(image)!=SIZE or image[:0x2600]!=stock[:0x2600]:raise ValueError('unknown/protected boot image')
    app=image[0x2600:0xc4000]
    if app!=stock[0x2600:0xc4000]:
        template=(ROOT/'analysis/build_04/image.bin').read_bytes()
        if sha(template)!=FINAL_SHA or app!=template[0x2600:0xc4000]:raise ValueError('unsupported application')
    return image

def plan(before,target):
    if len(before)!=SIZE or len(target)!=SIZE:raise ValueError('image must be 4 MiB')
    if before[:0xc4000]!=target[:0xc4000]:raise ValueError('artwork updater cannot change boot/application')
    if before[SETTINGS:SETTINGS+SECTOR]!=target[SETTINGS:SETTINGS+SECTOR]:raise ValueError('target settings must match known image')
    changed=[o for o in range(0,SIZE,SECTOR) if before[o:o+SECTOR]!=target[o:o+SECTOR]]
    if any(o<0xc4000 or o==SETTINGS for o in changed):raise ValueError('protected sector change')
    return dict(relocated=[o for o in changed if o>=0x2fe000],
                inplace=[o for o in changed if o<0x2fe000 and o!=DIRECTORY],
                directory=[o for o in changed if o==DIRECTORY])

def install(a,stock,before,target,groups):
    if not a.approved_phase or a.approve_sha256!=sha(target):raise ValueError('human-approved phase and reviewed image SHA-256 required')
    preceding=bytearray(before)
    for phase in PHASES:
        if phase==a.phase:break
        for off in groups[phase]:preceding[off:off+SECTOR]=target[off:off+SECTOR]
    selected=groups[a.phase]
    a.output.mkdir(parents=True,exist_ok=False);s=Session(a.output,stock)
    result=dict(completed=False,flash_written=False,phase=a.phase,candidate_sha256=sha(target),sectors=selected)
    mutated=False
    try:
        s.open();s.write_packet=512;readbuf=s.allocate(CHUNK+128)
        # Reads run even for an empty phase. No write-capable native code is installed then.
        ops=Operations(s,readbuf,s.allocate(SECTOR+128),selected[:1],resource=True) if selected else None
        read=lambda name,passno:fast_snapshot(ops,a.output,name,passno) if ops else snapshot(s,readbuf,a.output,name,passno)
        first=read('before-read-1.bin',0);second=read('before-read-2.bin',1)
        if first!=second:raise RuntimeError('independent backups differ; no flash writes')
        settings=first[SETTINGS:SETTINGS+SECTOR]
        if normalized(first,preceding[SETTINGS:SETTINGS+SECTOR])!=preceding:raise RuntimeError('live preimage differs outside settings; no flash writes')
        staged=bytearray(first)
        for off in selected:staged[off:off+SECTOR]=target[off:off+SECTOR]
        save(a.output/'before-expected.bin',first);save(a.output/'after-expected.bin',staged)
        journal(a.output,'phase_approved',phase=a.phase,candidate_sha256=sha(target),sectors=selected)
        for index,off in enumerate(selected,1):
            ops.approve([off]);old=first[off:off+SECTOR];new=bytes(staged[off:off+SECTOR]);raw=BEFORE+new+AFTER
            if ops.read(off,SECTOR,0x71)!=old:raise RuntimeError('sector changed before write')
            s.write(ops.source,raw,packet_size=512)
            for name in ('erase','program'):ops.refuse(name)
            if s.read(ops.source,len(raw))!=raw:raise RuntimeError('source changed before erase')
            journal(a.output,'sector_intent',address=off,before_sha256=sha(old),after_sha256=sha(new))
            s.check()
            if old!=bytes([255])*SECTOR:
                journal(a.output,'erase_intent',address=off);mutated=True;result['flash_written']=True
                status,seconds=ops.write('erase',off);journal(a.output,'erase_return',address=off,status=status,seconds=seconds)
                if status or ops.read(off,SECTOR,0x26)!=bytes([255])*SECTOR:raise RuntimeError('erase verification failed')
            if s.read(ops.source,len(raw))!=raw:raise RuntimeError('source changed before program')
            s.check();journal(a.output,'program_intent',address=off);mutated=True;result['flash_written']=True
            status,seconds=ops.write('program',off);journal(a.output,'program_return',address=off,status=status,seconds=seconds)
            if status or s.read(ops.source,len(raw))!=raw:raise RuntimeError('program/source check failed')
            for poison in (0x36,0xc9):
                if ops.read(off,SECTOR,poison)!=new:raise RuntimeError('sector readback mismatch')
            journal(a.output,'sector_verified',address=off);print(f'{a.phase}: {off:#08x} verified ({index}/{len(selected)})',flush=True)
        final=read('full-readback.bin',2)
        if final!=staged:raise RuntimeError('full phase readback mismatch')
        result.update(completed=True,reads_identical=True,backup_sha256=sha(first),readback_sha256=sha(final),
                      settings_preserved=final[SETTINGS:SETTINGS+SECTOR]==settings,
                      matches_target_with_live_settings=final==normalized(target,settings))
        journal(a.output,'whole_phase_verified',sha256=sha(final))
    except Exception as e:
        result['error']=str(e)
        if mutated:s.certain=False
        journal(a.output,'stopped',error=str(e),transport_certain=s.certain);raise
    finally:
        try:s.close()
        finally:(a.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')

def inspect(a,stock,before):
    a.output.mkdir(parents=True,exist_ok=False);s=Session(a.output,stock)
    report=dict(completed=False,flash_written=False)
    try:
        s.open();s.write_packet=512;buf=s.allocate(CHUNK+128)
        first=snapshot(s,buf,a.output,'flash-read-1.bin',0)
        if a.command=='backup':
            second=snapshot(s,buf,a.output,'flash-read-2.bin',1)
            report.update(reads_identical=first==second,read2_sha256=sha(second))
            if first!=second:raise RuntimeError('independent backups differ')
            save(a.output/'backup.bin',first)
        differing=[hex(o) for o in range(0,SIZE,SECTOR) if first[o:o+SECTOR]!=before[o:o+SECTOR]]
        report.update(read1_sha256=sha(first),known_sha256=sha(before),differing_sectors=differing,
                      settings_only_difference=all(int(o,16)==SETTINGS for o in differing))
        if not report['settings_only_difference']:raise RuntimeError('unknown live image outside settings; investigate')
        report['completed']=True
    except Exception as e:
        report['error']=str(e);journal(a.output,'stopped',error=str(e));raise
    finally:
        try:s.close()
        finally:(a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['plan','backup','verify','install'])
    p.add_argument('--known',type=Path,required=True,help='stock or exact previously installed full image')
    p.add_argument('--image',type=Path,help='target graphics image; application must equal --known')
    p.add_argument('--output',type=Path);p.add_argument('--phase',choices=PHASES)
    p.add_argument('--approved-phase',action='store_true');p.add_argument('--approve-sha256')
    a=p.parse_args();stock=reference();before=known_image(a.known,stock)
    if a.command in ('plan','install'):
        if a.image is None:p.error('--image required')
        target=known_image(a.image,stock);groups=plan(before,target)
        if a.command=='plan':
            print(json.dumps(dict(source_sha256=sha(before),target_sha256=sha(target),phases={k:[hex(o) for o in v] for k,v in groups.items()},protected='boot, application, live settings'),indent=2));return
        if a.phase is None:p.error('--phase required')
    if a.output is None:p.error('--output must be a new directory')
    if a.command=='install':install(a,stock,before,target,groups)
    else:inspect(a,stock,before)
if __name__=='__main__':main()
