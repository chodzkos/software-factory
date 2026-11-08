"""Konsument manifestu: prawdziwe git apply w pustym repo fixture, bez instalacji."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT/'hermes/ci/verify_native_overlay.py'
spec=importlib.util.spec_from_file_location('review_overlay_verifier',VERIFIER)
verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)

class OverlayVerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='review-overlay-');self.root=Path(self.tmp.name)
        self.native=self.root/'native';self.native.mkdir()
        self.env={'PATH':os.defpath,'HOME':str(self.root),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull}
        self.git('init','-q');(self.native/'module.py').write_text('value = 1\n')
        self.git('add','.');self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')
        head=self.git('rev-parse','HEAD').strip().decode();tree=self.git('rev-parse','HEAD^{tree}').strip().decode()
        (self.native/'module.py').write_text('value = 2\n');(self.native/'new.py').write_text('other = 3\n')
        self.git('add','-N','new.py');self.patch=self.root/'native_runtime.patch';self.patch.write_bytes(self.git('diff','--binary','--full-index'))
        files={n:{'sha256':hashlib.sha256((self.native/n).read_bytes()).hexdigest(),'mode':'0o644'} for n in ['module.py','new.py']}
        self.manifest=self.root/'manifest.json';self.data={'base_head':head,'base_tree':tree,'source_only':True,'installable':False,'patch_sha256':hashlib.sha256(self.patch.read_bytes()).hexdigest(),'files':files}
        self.save();self.git('reset','--hard','-q','HEAD')
    def tearDown(self):self.tmp.cleanup()
    def git(self,*args):return subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-C',str(self.native),*args],env=self.env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
    def save(self):self.manifest.write_text(json.dumps(self.data))
    def check(self,phase):return verifier.verify(self.native,self.manifest,self.patch,phase)
    def apply(self):self.git('apply','--check',str(self.patch));self.git('apply',str(self.patch))
    def test_original_bytes_then_applied_bytes_and_modes_pass(self):
        self.assertEqual(self.check('before')['files'],2);self.apply();self.assertTrue(self.check('after')['ok'])
    def test_tampered_patch_refused_before_any_apply_cli(self):
        self.patch.write_bytes(self.patch.read_bytes()+b'\n')
        result=subprocess.run([sys.executable,'-B',str(VERIFIER),'--native-root',str(self.native),'--manifest',str(self.manifest),'--patch',str(self.patch),'--phase','before'],env=self.env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('SHA-256 mismatch',result.stderr)
        self.assertEqual((self.native/'module.py').read_text(),'value = 1\n');self.assertFalse((self.native/'new.py').exists())
    def test_changed_base_target_refused(self):
        (self.native/'module.py').write_text('local edit\n')
        with self.assertRaisesRegex(verifier.OverlayError,'Git baseline'):self.check('before')
    def test_preexisting_new_target_refused(self):
        (self.native/'new.py').write_text('unexpected')
        with self.assertRaisesRegex(verifier.OverlayError,'already exists'):self.check('before')
    def test_applied_tampered_byte_refused(self):
        self.apply();(self.native/'new.py').write_text('tampered')
        with self.assertRaisesRegex(verifier.OverlayError,'hash or mode'):self.check('after')
    def test_applied_tampered_mode_refused(self):
        self.apply();(self.native/'new.py').chmod(0o755)
        with self.assertRaisesRegex(verifier.OverlayError,'hash or mode'):self.check('after')
    def test_applied_symlink_refused(self):
        self.apply();(self.native/'new.py').unlink();(self.native/'new.py').symlink_to(self.native/'module.py')
        with self.assertRaisesRegex(verifier.OverlayError,'symlink'):self.check('after')
    def test_missing_file_refused(self):
        self.apply();(self.native/'new.py').unlink()
        with self.assertRaises(FileNotFoundError):self.check('after')
    def test_manifest_cannot_omit_a_patched_file(self):
        del self.data['files']['new.py'];self.save()
        with self.assertRaisesRegex(verifier.OverlayError,'inventory'):self.check('before')
    def test_duplicate_manifest_key_refused(self):
        text=self.manifest.read_text();self.manifest.write_text(text[:-1]+', "installable": false}')
        with self.assertRaisesRegex(verifier.OverlayError,'duplicate'):self.check('before')
    def test_manifest_cannot_enable_installation(self):
        self.data['installable']=True;self.save()
        with self.assertRaisesRegex(verifier.OverlayError,'install-enabled'):self.check('before')
    def test_wrong_git_base_refused(self):
        self.data['base_head']='0'*40;self.save()
        with self.assertRaisesRegex(verifier.OverlayError,'Git base'):self.check('before')
    def test_unsafe_inventory_path_refused(self):
        self.data['files']['../outside']=self.data['files'].pop('new.py');self.save()
        with self.assertRaisesRegex(verifier.OverlayError,'unsafe'):self.check('before')

if __name__=='__main__':unittest.main(verbosity=2)
