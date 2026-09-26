"""Checked boot-region replacement PLAN, not a flash writer or release image.

Reuses the old automatic DestBin SD updater. The normal startup call remains;
the second, runtime updater wrapper is disabled to prevent late hook publication.
Full cold-boot / boot integrity / flash-writer coverage is still unverified.
"""
import hashlib
import struct
from analyze_firmware import EXPECTED_SHA256
from persistent_loader import build_loader
from persistent_payload import SIZE
from or1k_subset import signed
from ui_trace_patch import BIAS

BEGIN=0x1996c
END=0x19d48

def boot_plan(original,payload,size=SIZE,usb_recovery=False,ram_autostart=False):
    if hashlib.sha256(original).hexdigest()!=EXPECTED_SHA256:raise ValueError('wrong original firmware')
    # Scan the code-bearing prefix for direct branch entry into the replaced
    # routine. This is not proof that indirect/dynamically computed entries
    # cannot exist; that remains part of the boot validation gate.
    inbound=[]
    for off in range(0x2400,0x68b00,4):
        w=struct.unpack_from('<I',original,off)[0]
        if w>>26 in (0,1,3,4):
            target=off+signed(w&0x3ffffff,26)*4
            if BEGIN<=target<END and not BEGIN<=off<END:inbound.append((off,target,w>>26))
    if inbound!=[(0x2890,BEGIN,1),(0x19d54,BEGIN,1)]:
        raise ValueError(f'unexpected direct updater entry: {inbound}')
    if struct.unpack_from('<I',original,END-4)[0]!=0x44004800:raise ValueError('updater extent mismatch')
    code=build_loader(payload,BIAS+BEGIN,size=size,usb_recovery=usb_recovery)
    if len(code)>END-BEGIN:raise ValueError('loader does not fit updater region')
    replacement=code+struct.pack('<I',0x14000000)*((END-BEGIN-len(code))//4)
    edits=[(BEGIN,original[BEGIN:END],replacement,'boot-loader.bin','replace automatic SD firmware updater'),
           (0x28ac,struct.pack('<I',0x9c600006),struct.pack('<I',0x9c600003),
            'camera-start.bin','normal startup: menu 6 -> camera 3'),
           (0x19d54,struct.pack('<I',0x07ffff06),struct.pack('<I',0x9d600008),
            'disable-runtime-updater.bin','runtime SD updater returns 8; never run boot loader late')]
    if ram_autostart:
        from persistent_payload import unpack
        hooks=unpack(payload,expected_size=size)[2]
        if (BIAS+0x28ac,0x9c600006,0x9c600003,0) not in hooks:
            raise ValueError('RAM auto-start requires its checked literal payload hook')
        edits=[entry for entry in edits if entry[0]!=0x28ac]
    files={};records=[]
    for off,old,new,name,reason in edits:
        if original[off:off+len(old)]!=old or len(new)!=len(old):raise ValueError('patch preimage mismatch')
        files[name]=new
        records.append({'offset':off,'length':len(new),'file':name,'reason':reason,
            'original_sha256':hashlib.sha256(old).hexdigest(),
            'replacement_sha256':hashlib.sha256(new).hexdigest(),'applied':False})
    return files,{'status':'OFFLINE PLAN ONLY; NOT INSTALLED','loader_bytes':len(code),
        'available_code_bytes':END-BEGIN,'entry_RAM':BIAS+BEGIN,
        'direct_inbound_branches':inbound,'patches':records,
        'normal_startup_call_unchanged':True,'USB_mode9_branch_unchanged':True,
        'USB_startup_skips_custom_extension':usb_recovery,
        'RAM_autostart_hook':ram_autostart,
        'automatic_SD_firmware_updater_removed_in_plan':True,
        'runtime_updater_disabled_in_plan':True,'cold_boot_verified':False}
