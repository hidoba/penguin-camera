#!/usr/bin/env python3
"""Build an OFFLINE, UNVALIDATED full image and sector rollback inventory.

No USB, serial, flashing, or deployment. Preserves the verified latest settings.
The output is not an SD updater file: never rename it to DestBin.bin.
"""
import hashlib
import json

ORIGINAL_SHA = 'e22557a4497a1199c9ecc3956b18a89ac70af800b674055513f3de91bfb8f224'
BACKUP_SHA = '6a0bf0a5787d16ef41de703145e56150743be8d2c3aa7123a1880d2c1e1009fb'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def assemble(original, backup, bundle):
    if len(original) != 0x400000 or sha(original) != ORIGINAL_SHA:
        raise ValueError('Original dump changed')
    if len(backup) != len(original) or sha(backup) not in (BACKUP_SHA, ORIGINAL_SHA):
        raise ValueError('Base image must be the verified backup or the stock firmware')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if manifest['original_sha256'] != ORIGINAL_SHA or manifest['penguin_pack_version'] != 2:
        raise ValueError('Unsupported integration bundle')
    candidate = bytearray(backup)
    edits = []

    def asset(name):
        path = (bundle / name).resolve()
        if not path.is_relative_to(bundle.resolve()):
            raise ValueError('Asset escapes bundle')
        data = path.read_bytes()
        expected = manifest['inputs'][name]
        if len(data) != expected['bytes'] or sha(data) != expected['sha256']:
            raise ValueError(f'Asset changed: {name}')
        return data

    def patch(offset, data, old_hash, name):
        end = offset + len(data)
        if not data or offset < 0x2400 or end > 0x400000:
            raise ValueError('Patch outside allowed image bounds')
        if offset < 0x200000 and end > 0x1d7000:
            raise ValueError('Patch touches settings/internal photo storage')
        if any(offset < p['offset'] + p['bytes'] and p['offset'] < end for p in edits):
            raise ValueError('Overlapping patches')
        if sha(backup[offset:end]) != old_hash:
            raise ValueError(f'Patch preimage differs: {name}')
        candidate[offset:end] = data
        edits.append({'offset': offset, 'bytes': len(data), 'name': name,
                      'before_sha256': old_hash, 'after_sha256': sha(data)})

    ids = [p['resource'] for p in manifest['resource_changes']]
    # 64/65 welcome/goodbye + photo frames in stock frame slots 1..17 (update 36: any count)
    if len(set(ids)) != len(ids) or not {64, 65} <= set(ids) or not all(1 <= i <= 17 for i in set(ids)-{64, 65}):
        raise ValueError('Unexpected replacement resources')
    for entry in manifest['resource_changes']:
        data = asset(entry['file'])
        if len(data) > entry['slot_bytes'] or sha(data) != entry['payload_sha256']:
            raise ValueError('Invalid resource payload')
        patch(entry['offset'], data.ljust(entry['slot_bytes'], b'\xff'),
              entry['original_slot_sha256'], entry['file'])
    boot_count=2 if manifest['loader_patch_plan'].get('RAM_autostart_hook') else 3
    for key, directory, count in [('loader_patch_plan', 'boot-plan', boot_count),
                                   ('internal_storage_write_policy', 'storage-plan', 3),
                                   ('stock_flash_entry_guards', 'flash-guards', 5)]:
        records = manifest[key]['patches']
        if len(records) != count:
            raise ValueError('Missing safety patches')
        for entry in records:
            name = directory + '/' + entry['file']
            data = asset(name)
            if len(data) != entry['length'] or sha(data) != entry['replacement_sha256']:
                raise ValueError('Patch size/hash mismatch')
            patch(entry['offset'], data, entry['original_sha256'], name)
    regions = manifest['proposed_flash_regions']
    if len(regions) != 2 or [r['offset'] for r in regions] != [0x200000, 0x240000]:
        raise ValueError('Unexpected extension layout')
    for entry, name in zip(regions, ('native-effects-menu.pgfx', 'penguins.pgpack')):
        data = asset(name)
        if len(data) != entry['length']:
            raise ValueError('Extension length mismatch')
        patch(entry['offset'], data, sha(b'\xff' * len(data)), name)
    if candidate[:0x2400] != backup[:0x2400] or candidate[0x1d7000:0x200000] != backup[0x1d7000:0x200000]:
        raise ValueError('Protected data changed')
    if boot_count==2 and candidate[:0x3000]!=backup[:0x3000]:
        raise ValueError('RAM auto-start candidate must preserve complete shared boot-stub sector')
    sectors = []
    for offset in range(0, len(backup), 4096):
        old, new = backup[offset:offset + 4096], candidate[offset:offset + 4096]
        if old != new:
            sectors.append({'offset': offset, 'before_sha256': sha(old), 'after_sha256': sha(new),
                            'contains_boot_patch': offset in (0x2000, 0x19000)})
    return bytes(candidate), {'status': 'OFFLINE CANDIDATE ONLY — DO NOT FLASH',
        'release_ready': False, 'flash_written': False, 'cold_boot_verified': False,
        'source_backup_sha256': sha(backup), 'candidate_sha256': sha(candidate),
        'boot_header_preserved': True, 'settings_and_photo_metadata_preserved': True,
        'edits': edits, 'changed_sectors': sectors, 'blockers': manifest['blockers']}
