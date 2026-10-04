#!/usr/bin/env python3
"""Install one exact locally rebuilt MK1 effects candidate in two approved phases.
Never writes boot/settings/resources, resets USB, retries a failed write, or erases a chip.
The running RAM application stays intact until the user restarts after full verification.
"""
import argparse, json, os, time
from pathlib import Path
import build_mk1_effects as builder
from mk1_usb_flash import (Session,SIZE,SECTOR,CHUNK,SETTINGS,STOCK_SHA,BEFORE,AFTER,sha,save,journal,
    snapshot,read_flash,guard,ERASE,PROGRAM,FLUSH,ICACHE)
from mk1_fast_flash import Operations
ROOT=builder.ROOT

def plan(candidate):
    image,manifest=builder.build(); saved=(candidate/'image.bin').read_bytes()
    document=json.loads((candidate/'manifest.json').read_text())
    if saved!=image or document!=manifest: raise RuntimeError('candidate differs from exact local reproducible build')
    base=(ROOT/'analysis/build_01/image.bin').read_bytes(); stock=(ROOT/'flash_read1.bin').read_bytes()
    if sha(base)!=builder.BASE_SHA or sha(stock)!=STOCK_SHA: raise RuntimeError('reference hash mismatch')
    changed=[r['offset'] for r in manifest['changed_sectors']]
    if changed!=[o for o in range(0,SIZE,SECTOR) if image[o:o+SECTOR]!=base[o:o+SECTOR]]: raise RuntimeError('sector list mismatch')
    if any(o<0x3000 or o>=0xc4000 or o==SETTINGS for o in changed): raise RuntimeError('protected sector')
    if image[:0x2600]!=base[:0x2600] or image[0xc4000:]!=base[0xc4000:]: raise RuntimeError('protected bytes changed')
    # Retire old sketch before reusing its table; new code before publishing any hook.
    payload=[0x23000]+[o for o in changed if o<=builder.CAVE+builder.SIZE-1 and o+SECTOR>builder.CAVE]
    if any(o not in changed for o in payload): raise RuntimeError('retirement sector missing')
    activate=[o for o in changed if o not in payload]
    # USB selector is last; running USB code never gets modified in RAM.
    activate.sort(key=lambda o:(o==0x4b000,o))
    return stock,base,image,manifest,dict(payload=payload,activate=activate)

def fast_snapshot(ops,out,name,passno):
    data=bytearray()
    with (out/(name+'.partial')).open('xb') as f:
        for off in range(0,SIZE,CHUNK):
            if off%(512*1024)==0: ops.s.check()
            part=ops.read(off,CHUNK,((off//CHUNK)+127*passno)&255); data+=part; f.write(part)
            if (off+CHUNK)%(512*1024)==0: f.flush(); os.fsync(f.fileno())
        f.flush(); os.fsync(f.fileno())
    save(out/name,data); return bytes(data)

def install(args):
    if not args.approved_phase: raise RuntimeError('explicit user approval for this flash phase is required')
    if not args.rehearsal: raise RuntimeError('hardware pixel rehearsal report is required')
    rehearsal=json.loads((args.rehearsal/'report.json').read_text())
    if not rehearsal.get('completed') or rehearsal.get('flash_written') or rehearsal.get('live_hooks_installed'): raise RuntimeError('invalid rehearsal')
    stock,base,target,manifest,phases=plan(args.candidate)
    if rehearsal['candidate_sha256']!=sha(target):
        # A label-only revision can inherit the already executed pixel routines
        # only when the complete firmware differs in the two reviewed OSD words.
        if not args.label_revision: raise RuntimeError('rehearsal refers to a different candidate')
        revision=json.loads((args.label_revision/'report.json').read_text())
        old=(Path(revision['previous_candidate_dir'])/'image.bin').read_bytes()
        if not revision.get('completed') or revision.get('flash_written') or revision['candidate_sha256']!=sha(target):
            raise RuntimeError('RAM label revision was not verified for this candidate')
        if sha(old)!=rehearsal['candidate_sha256'] or sha(old)!=revision['previous_sha256']:
            raise RuntimeError('label revision predecessor differs from hardware rehearsal')
        delta=[o for o in range(0,len(target),4) if old[o:o+4]!=target[o:o+4]]
        if len(old)!=len(target) or delta!=[0x7b0b0,0x7b150] or revision['delta']!=delta:
            raise RuntimeError('revision changes more than the two reviewed label words')
        if target[delta[0]:delta[0]+4]!=bytes.fromhex('20a80cd8') or target[delta[1]:delta[1]+4]!=bytes.fromhex('2800ad9d'):
            raise RuntimeError('unexpected OSD revision words')
        revision_logs=[json.loads(v) for v in (args.label_revision/'journal.jsonl').read_text().splitlines()]
        if revision_logs[-1]['event']!='ram_cleanup_verified': raise RuntimeError('RAM label revision cleanup missing')
    records=[json.loads(v) for v in (args.rehearsal/'journal.jsonl').read_text().splitlines()]
    if not records or records[-1]['event']!='ram_cleanup_verified': raise RuntimeError('rehearsal cleanup not verified')
    preceding=bytearray(base)
    if args.phase=='activate':
        if not args.previous: raise RuntimeError('payload phase evidence required')
        r=json.loads((args.previous/'report.json').read_text())
        if not r.get('completed') or r.get('phase')!='payload' or r.get('candidate_sha256')!=sha(target): raise RuntimeError('wrong previous phase')
        before=(args.previous/'before-read-1.bin').read_bytes(); paired=(args.previous/'before-read-2.bin').read_bytes()
        if before!=paired or sha(before)!=r['backup_sha256']: raise RuntimeError('previous paired backup mismatch')
        preceding[SETTINGS:SETTINGS+SECTOR]=before[SETTINGS:SETTINGS+SECTOR]
        for o in phases['payload']: preceding[o:o+SECTOR]=target[o:o+SECTOR]
        if (args.previous/'full-readback.bin').read_bytes()!=preceding: raise RuntimeError('payload readback mismatch')
        logs=[json.loads(v) for v in (args.previous/'journal.jsonl').read_text().splitlines()]
        if logs[-1]['event']!='ram_cleanup_verified': raise RuntimeError('previous cleanup incomplete')
    args.output.mkdir(parents=True,exist_ok=False); s=Session(args.output,stock)
    result=dict(completed=False,phase=args.phase,flash_written=False,candidate_sha256=sha(target),sectors=phases[args.phase],
        pixel_rehearsal_candidate_sha256=rehearsal['candidate_sha256'],label_revision_evidence=str(args.label_revision) if args.label_revision else None)
    mutated=False
    try:
        s.open(); s.write_packet=512; readbuf=s.allocate(CHUNK+128); source=s.allocate(SECTOR+128)
        ops=Operations(s,readbuf,source,phases[args.phase])
        first=fast_snapshot(ops,args.output,'before-read-1.bin',0)
        if args.phase=='payload':
            second=fast_snapshot(ops,args.output,'before-read-2.bin',1)
            if first!=second: raise RuntimeError('fresh independent backups differ; no writes')
            result.update(reads_identical=True,live_prewrite_full_reads=2)
        else:
            # The immediately preceding payload phase already made two independent
            # full backups and a complete verified final read. Compare this new
            # full read against that exact final image below, carrying live settings.
            result.update(live_prewrite_full_reads=1,paired_backup_reference=str(args.previous),
                previous_full_readback_sha256=sha(preceding))
        normalized=bytearray(first); normalized[SETTINGS:SETTINGS+SECTOR]=preceding[SETTINGS:SETTINGS+SECTOR]
        if normalized!=preceding: raise RuntimeError('unknown live flash difference outside settings; no writes')
        # A newer settings value is deliberately carried through without ever erasing its sector.
        preceding[SETTINGS:SETTINGS+SECTOR]=first[SETTINGS:SETTINGS+SECTOR]
        final_target=bytearray(target); final_target[SETTINGS:SETTINGS+SECTOR]=first[SETTINGS:SETTINGS+SECTOR]
        staged=bytearray(preceding)
        for off in phases[args.phase]: staged[off:off+SECTOR]=target[off:off+SECTOR]
        save(args.output/'before-expected.bin',preceding); save(args.output/'after-expected.bin',staged)
        save(args.output/'final-target-live-settings.bin',final_target)
        result.update(backup_sha256=sha(first),
            settings_differ_from_pico=first[SETTINGS:SETTINGS+SECTOR]!=stock[SETTINGS:SETTINGS+SECTOR])
        journal(args.output,'phase_approved',phase=args.phase,sectors=phases[args.phase],candidate_sha256=sha(target))
        for index,off in enumerate(phases[args.phase],1):
            before=bytes(preceding[off:off+SECTOR]); after=bytes(staged[off:off+SECTOR]); raw=BEFORE+after+AFTER
            if ops.read(off,SECTOR,0x71)!=before: raise RuntimeError('sector changed before write')
            uploaded=time.monotonic(); s.write(source,raw,packet_size=512)
            upload_seconds=time.monotonic()-uploaded
            for name in ('erase','program'): ops.refuse(name)
            if s.read(source,len(raw))!=raw: raise RuntimeError('source changed before erase')
            journal(args.output,'sector_intent',address=off,before_sha256=sha(before),after_sha256=sha(after))
            s.check()
            journal(args.output,'erase_intent',address=off); mutated=True; result['flash_written']=True
            status,erase_seconds=ops.write('erase',off); journal(args.output,'erase_return',address=off,status=status,seconds=erase_seconds)
            if status or ops.read(off,SECTOR,0x26)!=bytes([255])*SECTOR: raise RuntimeError('erase verification failed')
            if s.read(source,len(raw))!=raw: raise RuntimeError('source changed before program')
            s.check()
            journal(args.output,'program_intent',address=off); status,program_seconds=ops.write('program',off)
            journal(args.output,'program_return',address=off,status=status,seconds=program_seconds)
            if status or s.read(source,len(raw))!=raw: raise RuntimeError('program/source check failed')
            for poison in (0x36,0xc9):
                if ops.read(off,SECTOR,poison)!=after: raise RuntimeError('sector readback mismatch')
            journal(args.output,'sector_verified',address=off,upload_seconds=upload_seconds,erase_seconds=erase_seconds,program_seconds=program_seconds)
            print(f'{args.phase}: {off:#08x} verified ({index}/{len(phases[args.phase])})',flush=True)
        final=fast_snapshot(ops,args.output,'full-readback.bin',2)
        if final!=staged: raise RuntimeError('full staged image differs')
        result.update(completed=True,readback_sha256=sha(final),settings_preserved=final[SETTINGS:SETTINGS+SECTOR]==first[SETTINGS:SETTINGS+SECTOR],
            matches_final_candidate_with_live_settings=final==final_target)
        journal(args.output,'whole_phase_verified',sha256=sha(final))
    except Exception as e:
        result['error']=str(e)
        if mutated: s.certain=False
        journal(args.output,'stopped',error=str(e),transport_certain=s.certain); raise
    finally:
        try: s.close()
        finally: (args.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('command',choices=['plan','install'])
    p.add_argument('--candidate',type=Path,required=True); p.add_argument('--phase',choices=['payload','activate'])
    p.add_argument('--approved-phase',action='store_true'); p.add_argument('--output',type=Path); p.add_argument('--previous',type=Path); p.add_argument('--rehearsal',type=Path)
    p.add_argument('--label-revision',type=Path,help='Verified two-word OSD-only RAM revision evidence, if using earlier unchanged pixel rehearsal')
    a=p.parse_args()
    if a.command=='plan':
        *_,m,phases=plan(a.candidate); print(json.dumps(dict(candidate_sha256=m['image_sha256'],phases=phases,protected='boot, settings and all resources'),indent=2)); return
    if not a.phase or not a.output: p.error('install requires --phase and --output')
    install(a)
if __name__=='__main__': main()
