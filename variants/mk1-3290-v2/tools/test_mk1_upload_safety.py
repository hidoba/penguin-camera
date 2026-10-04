"""Regression: corrupt program data must stop before any erase/program intent."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import mk1_usb_flash as flash

class FakeSession:
    def __init__(self,out,stock): self.out=out;self.certain=True;self.memory={};self.alloc=[];self.corrupt=False
    def open(self): pass
    def allocate(self,size):
        address=0x02300000+len(self.alloc)*0x20000;self.alloc.append(address);return address
    def write(self,address,data,**kw): self.memory[address]=bytes(data)
    def call(self,*args): pass
    def check(self,*args,**kw): pass
    def read(self,address,length):
        data=self.memory[address][:length]
        if self.corrupt and address==self.alloc[2]: return bytes([data[0]^1])+data[1:]
        return data
    def invoke(self,fn,args):
        if args and args[0]==0:return 0xffffffff
        if len(args)==3 and args[1]==self.alloc[2]+128:
            self.corrupt=True;return 0xffffffff
        raise AssertionError('Unexpected native call after corruption')
    def close(self): pass

class SafetyTests(unittest.TestCase):
    def test_corruption_stops_before_erase(self):
        stock,target,changed=flash.images()
        before=bytearray(stock)
        for phase,offsets in flash.phases(changed).items():
            if phase=='directory':break
            for off in offsets:before[off:off+4096]=target[off:off+4096]
        with tempfile.TemporaryDirectory() as td:
            backup=Path(td)/'backup';backup.mkdir()
            for name in ('backup.bin','flash-read-1.bin','flash-read-2.bin'):
                (backup/name).write_bytes(stock)
            (backup/'report.json').write_text(json.dumps(dict(completed=True,reads_identical=True,read1_sha256=flash.sha(stock))))
            (backup/'journal.jsonl').write_text(json.dumps(dict(event='ram_cleanup_verified'))+'\n')
            args=SimpleNamespace(approved_phase=True,backup=backup,phase='directory',resume_state=None,
                                 output=Path(td)/'out',source_packet=4096,paced_upload=True)
            with patch.object(flash,'Session',FakeSession),patch.object(flash,'snapshot',return_value=bytes(before)),patch.object(flash,'read_flash',return_value=stock[0xd1000:0xd2000]):
                with self.assertRaisesRegex(RuntimeError,'source changed before erase'):
                    flash.install(args,stock,target,changed)
            records=[json.loads(line) for line in (args.output/'journal.jsonl').read_text().splitlines()]
            self.assertFalse(any(r['event'] in ('sector_intent','erase_intent','program_intent') for r in records))
            self.assertFalse(json.loads((args.output/'report.json').read_text())['flash_written'])
if __name__=='__main__':unittest.main()
