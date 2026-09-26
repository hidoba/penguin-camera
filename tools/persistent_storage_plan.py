"""Offline no-internal-photo-write plan for this exact firmware.

Disable full photo-store mutators at their normal entries, including the format
path reachable BEFORE the loader runs. Preserve settings writes and all photo
data/metadata bytes. This is NOT a general low-level flash write firewall.
"""
import hashlib
import struct
from analyze_firmware import EXPECTED_SHA256
from or1k_subset import signed

ROUTINES=((0x39838,0x39a6c,'internal-photo-write-disabled.bin'),
          (0x39a6c,0x39b00,'internal-photo-delete-disabled.bin'),
          (0x39c04,0x39ca0,'internal-photo-format-disabled.bin'))


def storage_plan(original):
    if hashlib.sha256(original).hexdigest()!=EXPECTED_SHA256:raise ValueError('wrong firmware')
    files={};patches=[];entries=[]
    # Use status 1 for refusal (matching write/delete's failure convention).
    # Format callers/boot behavior still need hardware validation.
    refusal=struct.pack('<II',0x9d600001,0x44004800)  # r11=1; jr r9; no stack or RAM writes
    for start,end,name in ROUTINES:
        for off in range(0x2400,0x68b00,4):
            word=struct.unpack_from('<I',original,off)[0]
            if word>>26 not in (0,1,3,4):continue
            target=off+signed(word&0x3ffffff,26)*4
            if start<=target<end and not start<=off<end:
                if target!=start:raise ValueError(f'external entry into mutator body at {off:#x} -> {target:#x}')
                entries.append({'callsite':off,'target':target})
        files[name]=refusal
        patches.append({'offset':start,'length':8,'file':name,
            'reason':'refuse internal photo-store mutation; preserve existing data/index',
            'original_sha256':hashlib.sha256(original[start:start+8]).hexdigest(),
            'replacement_sha256':hashlib.sha256(refusal).hexdigest(),'applied':False})
    return files,{'status':'OFFLINE WRITE-ISOLATION PLAN; NOT INSTALLED OR COLD-BOOT VERIFIED',
        'patches':patches,'direct_external_entries':entries,
        'settings_writer_0x5408_unchanged':True,'photo_store_data_and_metadata_unchanged':True,
        'no_capacity_shrink_or_reformat':True,'low_level_program_erase_helpers_unchanged':True,
        'limitations':[
            'Direct-entry inventory does not prove absence of computed mid-function jumps or other peripheral write paths.',
            'Must be paired with removal of automatic/runtime SD updater; chip erase is otherwise still possible.',
            'Boot init may receive format failure if metadata is invalid; print-only boot/settings must be hardware-tested.',
            'Does not enforce a global ban on SD-card writes.'],
        'flash_written':False}
