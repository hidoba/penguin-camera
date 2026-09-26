"""Offline stock-SPI entry guards. Only the two settings pages may be changed.

Requires the existing internal-photo mutation refusals and updater removal.
Not a peripheral firewall: computed mid-function entries or direct SPI traffic
are outside this entry-point policy. Never installs or writes device flash.
"""
import hashlib
import struct
from analyze_firmware import EXPECTED_SHA256,parse_resources
from live_preview_patch import Code,BIAS
from or1k_subset import branch
from or1k_subset import signed

SETTINGS=0x1d7000
SETTINGS_RAM=0x02081ac0
CAVE=0x39858
CAVE_SIZE=0x180
PAGE=0x395f0
ERASE=0x3970c


def guard(original,origin,erase=False):
    a=Code();imm=a.immediate
    a.const(11,SETTINGS);a.compare(3,11,0);a.branch(4,'first')
    if not erase:
        a.const(11,SETTINGS+256);a.compare(3,11);a.branch(4,'deny')
        a.const(11,SETTINGS_RAM+256);a.compare(4,11);a.branch(4,'deny');a.branch(0,'allow')
    else:a.branch(0,'deny')
    a.label('first')
    if not erase:
        a.const(11,SETTINGS_RAM);a.compare(4,11);a.branch(4,'deny')
    a.label('allow');entry=ERASE if erase else PAGE
    for off in (entry,entry+4):a.emit(struct.unpack_from('<I',original,off)[0])
    a.emit(struct.unpack('<I',branch(origin+len(a.words)*4,BIAS+entry+8))[0])
    a.label('deny');imm(0x27,11,0,-1);a.emit(0x44004800)
    return a.finish()


def guard_plan(original):
    if hashlib.sha256(original).hexdigest()!=EXPECTED_SHA256:raise ValueError('Wrong original')
    _,resources=parse_resources(original)
    end=resources[-1]['offset']+resources[-1]['size']
    if (end+4095)&~4095!=SETTINGS:raise ValueError('Settings sector derivation changed')
    # This cave is inside an internal-photo writer already disabled at entry.
    # Ensure no other directly encoded branch enters its body or bypasses the
    # low-level function prefixes that these guards replace.
    spans=((0x39838,0x39a6c),(PAGE,0x39670),(ERASE,0x39778),
           (0x39778,0x397dc),(0x397dc,0x39818))
    calls=[]
    for off in range(0x2400,0x68b00,4):
        w=struct.unpack_from('<I',original,off)[0]
        if w>>26 not in (0,1,3,4):continue
        target=off+signed(w&0x3ffffff,26)*4
        for start,stop in spans:
            if start<=target<stop and not start<=off<stop:
                if target!=start:raise ValueError('Direct entry bypasses guarded function prefix')
                calls.append({'callsite':off,'target':target})
    page=guard(original,BIAS+CAVE)
    erase_offset=CAVE+0x100;erase=guard(original,BIAS+erase_offset,erase=True)
    if len(page)>0x100 or len(erase)>0x80:raise ValueError('Guard cave overflow')
    cave=bytearray(struct.pack('<I',0x14000000)*(CAVE_SIZE//4))
    cave[:len(page)]=page;cave[0x100:0x100+len(erase)]=erase
    refuse=struct.pack('<II',0x9d60ffff,0x44004800)
    edits=[(CAVE,bytes(cave),'settings-write-guards.bin','reuse disabled internal-photo writer body'),
        (PAGE,branch(BIAS+PAGE,BIAS+CAVE)+struct.pack('<I',0x14000000),'guard-page-program.bin','only two exact settings pages and source buffers'),
        (ERASE,branch(BIAS+ERASE,BIAS+erase_offset)+struct.pack('<I',0x14000000),'guard-sector-erase.bin','only exact settings sector'),
        (0x39778,refuse,'deny-block-erase.bin','deny all 64-KiB block erases'),
        (0x397dc,refuse,'deny-chip-erase.bin','deny all chip erases')]
    files={};patches=[]
    for off,data,name,reason in edits:
        files[name]=data;patches.append({'offset':off,'length':len(data),'file':name,'reason':reason,
            'original_sha256':hashlib.sha256(original[off:off+len(data)]).hexdigest(),
            'replacement_sha256':hashlib.sha256(data).hexdigest(),'applied':False})
    return files,{'status':'OFFLINE SETTINGS-ONLY STOCK SPI GUARDS; NOT INSTALLED',
        'patches':patches,'direct_entry_inventory':calls,
        'allowed_erase_sector':SETTINGS,'allowed_program_pages':[SETTINGS,SETTINGS+256],
        'allowed_program_sources':[SETTINGS_RAM,SETTINGS_RAM+256],
        'required_internal_photo_write_refusal':0x39838,
        'block_and_chip_erase_disabled':True,'hardware_verified':False,
        'limitations':['Guards normal stock helper entries, not arbitrary SPI peripheral access.',
            'Must accompany internal-photo mutation refusals and SD updater removal.',
            'Actual settings address and cold-boot/settings behavior need hardware verification.',
            'Future authorized USB updater needs a separate bounded write path; stock writes are intentionally blocked.']}
