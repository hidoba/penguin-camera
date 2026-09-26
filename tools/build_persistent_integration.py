#!/usr/bin/env python3
"""Assemble a NON-FLASHABLE integration bundle. No USB/flash-writing support.

Contains checked resource replacements, relocatable native code, embedded
penguins and a proposed flash layout. Explicit blockers prevent confusing this
work-in-progress with a complete firmware image.
"""
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile
from PIL import Image
from analyze_firmware import EXPECTED_SHA256,parse_resources
from replace_resource import replace
from prepare_release_artwork import validate_pack
from persistent_payload import package,unpack,BASE,SIZE
from menu_target import DRAW,FOOTER,ITEMS
from or1k_subset import CPU
from persistent_boot_plan import boot_plan

ROOT=Path(__file__).resolve().parents[1]
def sha(data):return hashlib.sha256(data).hexdigest()
def align(value):return (value+4095)&~4095

def assemble(assets,out,print_assets=None,usb_recovery=False,ram_autostart=False,*,penguin_corrections=False,regression_fixes=False,ui_cleanup=False,random_timing=False,preview_speed=False,effects_v5=False,frames=None,penguins=None):
    if out.exists() or out.with_suffix('.zip').exists():raise ValueError('output already exists')
    if penguin_corrections and print_assets is None:raise ValueError('Penguin corrections require print assets')
    if regression_fixes and print_assets is None:raise ValueError('Regression fixes require combined payload')
    if ui_cleanup and print_assets is None:raise ValueError('UI cleanup requires combined payload')
    if random_timing and print_assets is None:raise ValueError('Timing-based random selection requires combined payload')
    if preview_speed and (print_assets is None or not regression_fixes):raise ValueError('Preview speed requires combined regression fixes')
    if ram_autostart and (print_assets is None or not usb_recovery):
        raise ValueError('RAM auto-start requires full collection and USB bypass')
    original=(ROOT/'flash_zb25vq32_read1.bin').read_bytes()
    if sha(original)!=EXPECTED_SHA256:raise ValueError('original hash mismatch')
    second=ROOT/'flash_zb25vq32_read2.bin'
    if second.exists() and second.read_bytes()!=original:raise ValueError('backup mismatch')
    prepared=json.loads((assets/'manifest.json').read_text())
    if prepared['original_sha256']!=EXPECTED_SHA256:raise ValueError('asset source mismatch')
    _,entries=parse_resources(original)
    files={};changes=[];ranges=[]
    items=prepared['replacements'];frame_slots=None
    if frames is not None:
        # Update 36: the frames folder replaces the prepared frame artwork (slots 1..17).
        import frames as frames_module
        built=frames_module.prepare(original,frames)
        frame_slots=[rid for _,_,rid,_,_ in built]
        items=[i for i in items if not 1<=i['index']<=frames_module.COUNT]
        items+=[{'index':rid,'output':f'frame-{number:02d}-{Path(path).stem}.jpg','payload':payload,'frame':number,
                 'source':Path(path).name,'audit':audit} for number,path,rid,payload,audit in built]
    for item in items:
        payload=item['payload'] if 'payload' in item else (assets/item['output']).read_bytes()
        if 'payload' not in item and sha(payload)!=item['sha256']:raise ValueError('replacement hash mismatch')
        _,entry=replace(original,item['index'],payload)
        start=entry['offset'];end=start+entry['size']
        if any(start<b and a<end for a,b in ranges):raise ValueError('overlapping resource patches')
        ranges.append((start,end))
        name='resources/'+Path(item['output']).name;files[name]=payload
        changes.append({'resource':item['index'],'file':name,'offset':start,'slot_bytes':entry['size'],
                        'payload_bytes':len(payload),'padding':'ff after payload',
                        'original_slot_sha256':sha(original[start:end]),'payload_sha256':sha(payload)})
        if 'frame' in item:changes[-1].update(frame=item['frame'],source=item['source'],frame_audit=item['audit'])
    expected_ids={6,7,8,9,64,65} if frame_slots is None else {64,65}|set(frame_slots)
    if {item['resource'] for item in changes}!=expected_ids:raise ValueError('missing/unexpected replacement set')
    payload_size=SIZE
    if print_assets is None:
        penguins=(assets/'penguins.pgpack').read_bytes()
        if sha(penguins)!=prepared['pack_sha256']:raise ValueError('penguin hash mismatch')
        count=validate_pack(penguins)
        effects,meta=package(original)
    else:
        from prepare_print_penguins import validate
        from persistent_penguins import package as combined_package,SIZE as payload_size,PACK_FLASH
        if penguins is not None:
            # Update 37: the penguins folder is the Random-penguin collection (1..16 pictures).
            import penguins as penguins_module
            penguins,print_manifest=penguins_module.build(penguins)
            count=len(validate(penguins))
        else:
            print_manifest=json.loads((print_assets/'manifest.json').read_text())
            penguins=(print_assets/'penguins-print.pgpack').read_bytes()
            if print_manifest.get('version')!=2 or sha(penguins)!=print_manifest['pack_sha256']:
                raise ValueError('print-layout pack mismatch')
            count=len(validate(penguins))
            if count!=len(prepared['penguins']) or count!=len(print_manifest['penguins']):raise ValueError('image count mismatch')
            for index,(old,new) in enumerate(zip(prepared['penguins'],print_manifest['penguins'])):
                if old['id']!=index+1 or new['id']!=index+1 or old['source_sha256']!=new['source_sha256']:
                    raise ValueError('print assets do not match user collection')
        effects,meta=combined_package(original,penguins,ram_autostart=ram_autostart,
                                     penguin_corrections=penguin_corrections,regression_fixes=regression_fixes,
                                     ui_cleanup=ui_cleanup,random_timing=random_timing,preview_speed=preview_speed,effects_v5=effects_v5,
                                     frame_slots=frame_slots)
        files['penguin-assets-manifest.json']=(json.dumps(print_manifest,indent=2)+'\n').encode()
    blob,_,_=unpack(effects,expected_size=payload_size)
    files['native-effects-menu.pgfx']=effects;files['penguins.pgpack']=penguins
    boot_files,loader_plan=boot_plan(original,effects,size=payload_size,usb_recovery=usb_recovery,ram_autostart=ram_autostart)
    for name,data in boot_files.items():files['boot-plan/'+name]=data
    storage_policy=None
    flash_guards=None
    if print_assets is not None:
        from persistent_storage_plan import storage_plan
        policy_files,storage_policy=storage_plan(original)
        for name,data in policy_files.items():files['storage-plan/'+name]=data
        from persistent_flash_guard import guard_plan
        guard_files,flash_guards=guard_plan(original)
        for name,data in guard_files.items():files['flash-guards/'+name]=data
    # Proposed storage only. Erased bytes are not proof stock code never uses
    # them; no destination is written by this builder.
    fx_start=0x200000;pg_start=align(fx_start+len(effects)) if print_assets is None else PACK_FLASH;end=pg_start+len(penguins)
    if end>len(original):raise ValueError(f'penguin pictures too large: {len(penguins)} bytes, {len(original)-pg_start} available')
    if fx_start+((len(effects)+15)&~15)>pg_start:raise ValueError('payload overlaps image pack')
    if end>len(original) or any(v!=255 for v in original[fx_start:align(end)]):
        raise ValueError('proposed extension overlaps non-erased/out-of-bounds flash')
    # Confirm mode IDs through the stock registration table names, not guesses.
    for appid,table,label in ((3,0x80540,b'Photo Encode'),(6,0x82694,b'Main menu')):
        pointer=struct.unpack_from('<I',original,table)[0]-0x02000000+0x2400
        if original[pointer:pointer+len(label)+1]!=label+b'\0':raise ValueError('mode table mismatch')
    expected=struct.pack('<I',0x9c600006)
    if original[0x28ac:0x28b0]!=expected:raise ValueError('startup selector changed')
    boot={'offset':0x28ac,'expected_hex':expected.hex(),'replacement_hex':struct.pack('<I',0x9c600003).hex(),
          'meaning':'standalone startup: Main menu (6) -> Photo Encode (3)',
          'usb_mode9_branch_unchanged':True,'applied':False,'cold_boot_verified':False}
    boot['placement']='checked RAM hook after successful loader' if ram_autostart else 'flash word replacement'
    blockers=meta['release_blockers']+[
        'boot integrity requirements and a recovery/programming path not verified',
        'proposed upper-flash layout not audited against all stock flash writers',
        'global no-card-write enforcement and game/menu removal not complete']
    manifest={'status':'INTEGRATION BUNDLE ONLY — NOT A FLASHABLE IMAGE','release_ready':False,
              'original_sha256':EXPECTED_SHA256,'menu':{'items':ITEMS,'footer':FOOTER,
                  'native_renderer':'OpenRISC landscape pool-0 adapter and stock event hooks; RAM display/navigation verified in attempt 06; cold boot pending'},
              'resource_changes':changes,'startup_patch_plan':boot,'effects':meta,
              'loader_patch_plan':loader_plan,
              'internal_storage_write_policy':storage_policy,
              'stock_flash_entry_guards':flash_guards,
              'penguin_count':count,'penguin_orientation':prepared['orientation'],
              'penguin_pack_version':1 if print_assets is None else 2,
              'random_penguin_implementation':'queued request only' if print_assets is None else 'native flash-backed worker; offline tested, not hardware installed',
              'loader_requested_bytes':((len(effects)+15)&~15)+32,
              'loader_allocation_bytes':((((len(effects)+15)&~15)+32)+63)&~63,
              'proposed_flash_regions':[{'name':'effects/menu payload','offset':fx_start,'length':len(effects)},
                                       {'name':'penguin pack','offset':pg_start,'length':len(penguins)}],
              'upper_2MiB_bytes_used_including_alignment':align(end)-fx_start,
              'remaining_upper_2MiB_bytes':0x400000-align(end),
              'blockers':blockers,'flash_written':False,'live_RAM_written':False,
              'inputs':{name:{'bytes':len(payload),'sha256':sha(payload)} for name,payload in files.items()}}
    # Render the actual generated native instructions, not an artistic mockup.
    for selection in range(3):
        pixels=bytearray(76800);stack=bytearray(4096)
        cpu=CPU([(BASE,bytearray(blob),False),(0x02130000,pixels,True),(0x021ff000,stack,True)])
        cpu.r[1]=0x02200000;cpu.r[3:5]=[0x02130000,selection]
        cpu.run(BASE+DRAW,limit=1000000)
        if cpu.r[11]!=0:raise ValueError('menu renderer failed')
        stream=io.BytesIO();Image.frombytes('L',(320,240),bytes(pixels)).save(stream,format='PNG')
        files[f'menu-preview-{selection}.png']=stream.getvalue()
    files['manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    files['README.md']=('''# Persistent integration work-in-progress

NOT a firmware image. NOT installed. Do not rename to DestBin.bin or flash.

This bundles all six user replacements (including four photo frames), all 14
penguins, a relocatable OpenRISC effects payload, a native text-menu renderer,
and the proposed standalone auto-start patch. Read manifest.json for exact
storage layout, hashes, available components and remaining release blockers.

The menu PNGs are outputs from executing the generated OpenRISC renderer in
the project's limited interpreter. They are not JPEG resources used at runtime.
Menu display/buttons were physically verified in RAM attempt 06, including
Camera/Settings entry and return. See manifest for the Random penguin implementation:
the RAM resident-image button print is user-confirmed; a flash-backed worker,
when included, is offline-tested only and requires combined hardware validation.
The boot-plan folder contains disjoint replacement regions, NOT an update file.
It reuses the automatic SD updater and disables its runtime wrapper. Placement,
boot integrity and cold-start behavior still need hardware validation.
When present, storage-plan disables stock internal-photo write/delete/format
entries without changing stored photos, metadata or the settings writer. This
is a reviewed-entry plan, not a complete low-level flash-write firewall; it has
not been installed or cold-boot tested. See its limitations in the manifest.
The full-collection payload keeps the earlier cracked approximation as
CRACKED EXPERIMENTAL at the user's request; it is not an exact Ditherboy match.
The flash-guards folder restricts normal stock page-program/sector-erase entries
to the two settings pages/the settings sector and refuses block/chip erases.
It requires the storage-plan refusals, is not a peripheral firewall, and has
not yet been verified on hardware.
Read-back, cold-boot, timing, decoder and recovery tests remain mandatory.

No games or original resources were deleted, and no firmware binary or flashing
command is supplied. Original flash dumps remain byte-identical.
''').encode()
    if usb_recovery:
        files['README.md']+=('''
## USB-startup extension bypass

When the stock startup process flag is USB (2), the loader returns before
allocating or reading the custom payload. The original startup then selects
USB mode 9. Standalone startup still loads the custom payload and enters Camera.
This is intended to retain stock USB access if a custom extension is faulty;
it is not boot-ROM recovery and cannot rescue damaged stock startup/USB code.
The bypass and standalone loader are host-tested; full cold boot is still pending.
To use custom features normally, power up without USB first. This also means
booting directly with USB does not install custom handlers for that session.
''').encode()
    if ram_autostart:
        files['README.md']+=b'\nAuto-start is a checked RAM hook published only after successful payload validation. Flash sector 0x2000 is not modified; an extension rejection retains stock menu startup.\n'
    out.mkdir(parents=True)
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',zipfile.ZIP_DEFLATED) as archive:
        for name,payload in sorted(files.items()):
            path=out/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
            archive.writestr(out.name+'/'+name,payload)
    print(json.dumps({'output':str(out),'release_ready':False,'resource_count':len(changes),
                      'penguins':count,'proposed_flash_bytes':manifest['upper_2MiB_bytes_used_including_alignment'],
                      'remaining_bytes':manifest['remaining_upper_2MiB_bytes']},indent=2))
    return manifest
