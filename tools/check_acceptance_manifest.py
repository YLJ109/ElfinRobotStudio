"""Evidence is feature-level, version bound and never promotes field qualification."""
from _offline_guard import require_offline_copy
require_offline_copy()
import json
from pathlib import Path
import tempfile
import unittest
from backend.application.acceptance import read_acceptance


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'acceptance.json'
        self.identity={'verified':True,'backend_sha256':'a','frontend_sha256':'b'}
        self.value={'version':1,'backend_sha256':'a','frontend_sha256':'b','features':[{'id':'vision','title':'视觉','source':True,'offline':True,'ui':False,'field':False,'unverified':['真实相机'],'evidence':['output/checks/current.json']}]}
    def write(self):self.path.write_text(json.dumps(self.value),encoding='utf-8')
    def test_missing_never_fabricates_evidence(self):
        result=read_acceptance(self.path,self.identity);self.assertIsNone(result['evidence']);self.assertFalse(result['matches_current'])
    def test_only_exact_both_verified_identities_match(self):
        self.write();result=read_acceptance(self.path,self.identity);self.assertTrue(result['matches_current']);self.assertFalse(result['evidence']['features'][0]['field'])
        for key in ('backend_sha256','frontend_sha256'):
            self.assertFalse(read_acceptance(self.path,{**self.identity,key:'different'})['matches_current'])
        self.assertFalse(read_acceptance(self.path,{**self.identity,'verified':False})['matches_current'])
    def test_malformed_or_unbounded_evidence_is_unavailable(self):
        for item in ({**self.value,'features':[{}]}, {**self.value,'features':self.value['features']*81}):
            self.path.write_text(json.dumps(item),encoding='utf-8');self.assertIsNone(read_acceptance(self.path,self.identity)['evidence'])
        self.path.write_text('x'*(128*1024+1));self.assertIsNone(read_acceptance(self.path,self.identity)['evidence'])


if __name__=='__main__':unittest.main()
