"""Graphics updater policy and failure checks. Entirely simulated; no USB access."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import mk1_artwork_flash as f
from mk1_fast_flash import writer
from mk1_native import CPU
from mk1_usb_flash import ERASE,PROGRAM,BEFORE,AFTER

class FakeSession:
    current=None; live=None; corrupt=False
    def __init__(self,out,stock):
        self.out=out;self.stock=stock;self.certain=True;self.ram={};self.next=0x02300000
        self.calls=[];self.flash=bytearray(type(self).live);type(self).current=self
    def open(self):pass
    def allocate(self,n):p=self.next;self.next+=0x20000;return p
    def check(self,*args,**kwargs):pass
    def write(self,p,data,**kwargs):
        data=bytes(data)
        if type(self).corrupt and len(data)==4096+128:data=data[:64]+bytes([data[64]^1])+data[65:]
        self.ram[p]=data
    def read(self,p,n):return self.ram[p][:n]
    def close(self):self.closed_certain=self.certain

class FakeOps:
    def __init__(self,s,buf,source,sectors,resource=False):
        assert resource;self.s=s;self.source=source;self.allowed=sectors
    def approve(self,sectors):self.allowed=sectors
    def read(self,off,n,poison):return bytes(self.s.flash[off:off+n])
    def refuse(self,name):pass
    def write(self,name,off):
        assert off in self.allowed;self.s.calls.append((name,off))
        self.s.flash[off:off+4096]=bytes([255])*4096 if name=='erase' else self.s.ram[self.source][64:-64]
        return 0,0.01

def snapshot(ops,out,name,passno):
    data=bytes(ops.s.flash);f.save(out/name,data);return data

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.before=bytes([255])*f.SIZE;self.target=bytearray(self.before)
        self.target[0x2fe000]=1;self.target[0x140000]=2;self.target[f.DIRECTORY]=3
        FakeSession.corrupt=False
    def test_plan_directory_last_and_rejects_boot_app_settings(self):
        groups=f.plan(self.before,bytes(self.target))
        self.assertEqual(groups,dict(relocated=[0x2fe000],inplace=[0x140000],directory=[f.DIRECTORY]))
        for off in (0,0x2600,0x78000,f.SETTINGS):
            bad=bytearray(self.target);bad[off]^=1
            with self.assertRaises(ValueError):f.plan(self.before,bytes(bad))
    def execute(self,phase,live,corrupt=False):
        FakeSession.live=live;FakeSession.corrupt=corrupt
        with tempfile.TemporaryDirectory() as td:
            args=SimpleNamespace(phase=phase,output=Path(td)/'run',approved_phase=True,approve_sha256=f.sha(self.target))
            error=None
            with patch.object(f,'Session',FakeSession),patch.object(f,'Operations',FakeOps),patch.object(f,'fast_snapshot',snapshot):
                try:f.install(args,self.before,self.before,bytes(self.target),f.plan(self.before,bytes(self.target)))
                except RuntimeError as e:error=e
            report=json.loads((args.output/'report.json').read_text())
            logs=[json.loads(l) for l in (args.output/'journal.jsonl').read_text().splitlines()]
            return error,report,logs,FakeSession.current
    def test_live_settings_preserved_and_every_phase_verified(self):
        live=bytearray(self.before);live[f.SETTINGS+20]=7
        for phase in f.PHASES:
            error,report,logs,s=self.execute(phase,bytes(live))
            self.assertIsNone(error);self.assertTrue(report['completed']);self.assertTrue(report['settings_preserved'])
            self.assertEqual(s.flash[f.SETTINGS+20],7)
            live=s.flash
        self.assertTrue(report['matches_target_with_live_settings'])
        self.assertEqual(s.calls,[('program',f.DIRECTORY)])
    def test_unknown_preimage_and_missing_phase_stop_before_writes(self):
        bad=bytearray(self.before);bad[0x141000]=1
        for phase,live in [('relocated',bytes(bad)),('directory',self.before)]:
            error,report,logs,s=self.execute(phase,live)
            self.assertRegex(str(error),'live preimage differs');self.assertFalse(report['flash_written']);self.assertFalse(s.calls)
    def test_corrupt_ram_source_stops_before_erase_or_program(self):
        error,report,logs,s=self.execute('relocated',self.before,True)
        self.assertRegex(str(error),'source changed before erase');self.assertFalse(report['flash_written']);self.assertFalse(s.calls)
        self.assertFalse(any(v['event'] in ('erase_intent','program_intent') for v in logs))
    def test_approval_hash_must_match_before_opening_hardware(self):
        with tempfile.TemporaryDirectory() as td:
            a=SimpleNamespace(approved_phase=True,approve_sha256='wrong',phase='relocated',output=Path(td)/'run')
            with patch.object(f,'Session') as session:
                with self.assertRaisesRegex(ValueError,'reviewed image SHA-256'):
                    f.install(a,self.before,self.before,bytes(self.target),f.plan(self.before,bytes(self.target)))
                session.assert_not_called()
            self.assertFalse(a.output.exists())
    def test_full_readback_failure_retains_uncertain_session(self):
        def bad_final(ops,out,name,passno):
            data=bytearray(snapshot(ops,out,name,passno))
            if name=='full-readback.bin':data[0x141000]^=1
            return bytes(data)
        with patch.object(f,'fast_snapshot',bad_final):
            # execute normally supplies its own snapshot patch; invoke explicitly here.
            with tempfile.TemporaryDirectory() as td:
                FakeSession.live=self.before
                a=SimpleNamespace(approved_phase=True,approve_sha256=f.sha(self.target),phase='relocated',output=Path(td)/'run')
                with patch.object(f,'Session',FakeSession),patch.object(f,'Operations',FakeOps):
                    with self.assertRaisesRegex(RuntimeError,'full phase readback mismatch'):
                        f.install(a,self.before,self.before,bytes(self.target),f.plan(self.before,bytes(self.target)))
                self.assertFalse(FakeSession.current.closed_certain)
                report=json.loads((a.output/'report.json').read_text())
                self.assertTrue(report['flash_written']);self.assertFalse(report['completed'])
    def test_independent_backup_mismatch_stops_before_writes(self):
        def differing_read(ops,out,name,passno):
            data=bytearray(snapshot(ops,out,name,passno))
            if passno==1:data[0x2fe000]^=1
            return bytes(data)
        with tempfile.TemporaryDirectory() as td:
            FakeSession.live=self.before
            a=SimpleNamespace(approved_phase=True,approve_sha256=f.sha(self.target),phase='relocated',output=Path(td)/'run')
            with patch.object(f,'Session',FakeSession),patch.object(f,'Operations',FakeOps),patch.object(f,'fast_snapshot',differing_read):
                with self.assertRaisesRegex(RuntimeError,'independent backups differ'):
                    f.install(a,self.before,self.before,bytes(self.target),f.plan(self.before,bytes(self.target)))
            self.assertFalse(FakeSession.current.calls)
    def test_native_resource_guard_accepts_exact_resource_and_denies_settings(self):
        origin=0x02300000;result=origin+0x2000;source=origin+0x4000
        for allowed in (0xc4000,0xd1000,0x3ff000):
            for request in (allowed,0,0x78000,f.SETTINGS,allowed+1):
                memory=bytearray(0x10000);hit=[]
                blob=writer(origin,result,source,[allowed],True,resource=True);memory[:len(blob)]=blob
                memory[0x2000:0x2008]=(request.to_bytes(4,'little')+(request^0xffffffff).to_bytes(4,'little'))
                memory[0x4000:0x5080]=BEFORE+bytes(4096)+AFTER
                class GuardCPU(CPU):
                    def access(self,a,n,value=None):
                        if value is None and n==4 and a in (ERASE,PROGRAM):hit.append(self.r[3:6]);self.r[11]=0;return 0x44004800
                        return super().access(a,n,value)
                cpu=GuardCPU([(origin,memory,True)]);cpu.r[1]=origin+0xf000;cpu.r[3]=result;cpu.r[4]=8;cpu.run(origin)
                self.assertEqual(hit,[[allowed,source+64,4096]] if request==allowed else [])
        for protected in (0,0x78000,f.SETTINGS,0x400000):
            with self.assertRaises(ValueError):writer(origin,result,source,[protected],True,resource=True)
if __name__=='__main__':unittest.main()
