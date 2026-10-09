import json
import sys
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import voice_inbox as vi

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        config={'schema_version':'voice-routing-config/v1','projects':{}}
        rules={'schema_version':'voice-routing/v1','version':1,'rules':[{'route_id':'queue','project_id':None,'operation':'queued','natures':['other_project','unintelligible']}]}
        vi.write(self.root/'routing/config.json',config);vi.write(self.root/'routing/rules.json',rules)
        source={'raw_text':'实验试剂用完了。','audio_sha256':'a'*64,'sha256':'b'*64,'audio_path':'/source.m4a','recording_id':'rec-1'}
        self.source=source;self.rules=rules
    def proposal(self,**extra):
        return {'schema_version':'voice-routing-proposal/v1','rules_sha256':vi.digest(self.rules),'items':[{'route_id':'queue','source_sha256':'a'*64,'quote':self.source['raw_text'],'item_key':'one','ai_draft':'需要补充实验试剂','nature':'other_project','pipeline_hint':'实验材料',**extra}]}
    def test_queued_repeat_keeps_source_and_groups_topics(self):
        with patch.object(vi,'sources',return_value=({'a'*64:self.source},[])):
            first=vi.apply(self.root,self.proposal());second=vi.apply(self.root,self.proposal())
            self.assertTrue(first['actionable_change']);self.assertFalse(second['actionable_change'])
            self.assertEqual(second['results'][0]['status'],'duplicate')
            self.assertEqual(vi.prepare(self.root)['sources'],[])
            self.assertEqual(vi.status(self.root)['groups'][0]['topic'],'实验材料')
    def test_bad_rules_fabricated_confirmation_and_partial_source_retry(self):
        with patch.object(vi,'sources',return_value=({'a'*64:self.source},[])):
            bad=self.proposal();bad['rules_sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'rules_version'):vi.apply(self.root,bad)
            p=self.proposal();p['items'].append({**p['items'][0],'item_key':'two','quote':'not in recording'})
            result=vi.apply(self.root,p)
            self.assertEqual(result['results'][1]['status'],'rejected')
            self.assertEqual(len(vi.prepare(self.root)['sources']),1)
            result=vi.apply(self.root,self.proposal(human_confirmed=True))
            self.assertIn('cannot_claim',result['results'][0]['reason'])
    def test_pipeline_decision_records_exact_approval_but_never_enables(self):
        proposal=self.root/'routing/proposals/one.json'
        vi.write(proposal,{'schema_version':'voice-pipeline-proposal/v1','proposal_id':'one'})
        phash=hashlib.sha256(proposal.read_bytes()).hexdigest()
        owner=self.root/'owner.json'
        vi.write(owner,{'kind':'user_message','text':'approve this exact pipeline proposal','message_ref':'synthetic-turn',
                       'known_at':'2026-10-08T00:00:00.000Z','scope':'new_pipeline','action':'approve','proposal_id':'one','proposal_sha256':phash})
        decision={'scope':'new_pipeline','action':'approve','proposal_path':str(proposal),'proposal_sha256':phash,
                  'evidence':{'path':str(owner),'sha256':hashlib.sha256(owner.read_bytes()).hexdigest()}}
        first=vi.record_decision(self.root,decision);again=vi.record_decision(self.root,decision)
        self.assertEqual(first['status'],'recorded');self.assertEqual(again['status'],'duplicate')
        self.assertFalse(first['pipeline_enabled'])
        changed={**decision,'proposal_sha256':'0'*64}
        with self.assertRaisesRegex(ValueError,'proposal_version'):vi.record_decision(self.root,changed)

    def test_unresolved_destination_keeps_its_review_stage(self):
        vi.write(self.root/'routing/proposals/undirected.json',{
            'schema_version':'voice-pipeline-proposal/v1','proposal_id':'undirected',
            'status':'needs_destination_or_intent','review_stage':'needs_destination_or_intent'})
        proposal=vi.pipeline_proposals(self.root)[0]
        self.assertEqual(proposal['status'],'needs_destination_or_intent')
        self.assertIsNone(proposal['candidate_project'])

    def test_pipeline_status_requires_matching_rule_replay_and_implementation_evidence(self):
        path=self.root/'routing/proposals/p03.json'
        vi.write(path,{'schema_version':'voice-pipeline-proposal/v1','proposal_id':'p03','candidate_project':'body_building'})
        proposal_sha=hashlib.sha256(path.read_bytes()).hexdigest()
        vi.write(self.root/'routing/decisions/approved.json',{'status':'approved_for_implementation',
            'known_at':'2026-10-08T00:00:00.000Z','decision':{'proposal_sha256':proposal_sha,'scope':'new_pipeline'}})
        self.rules['rules'].append({'route_id':'meal','project_id':'body_building','operation':'meal_log',
                                     'natures':['diet_record'],'approved_proposal_sha256':proposal_sha})
        vi.write(self.root/'routing/rules.json',self.rules)
        vi.write(self.root/'routing/ledger.json',{'schema_version':'voice-routing-ledger/v1','entries':{
            'voice-meal':{'status':'archived','route_id':'meal'}}})
        evidence=self.root/'tested.txt';evidence.write_text('consumer tests passed')
        proof={'schema_version':'voice-proposal-implementation/v1','proposal_sha256':proposal_sha,
               'scope':'new_pipeline','route_id':'meal','replay_event_id':'voice-meal',
               'evidence':[{'path':str(evidence),'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
        vi.write(self.root/'routing/enablements/p03.json',proof)
        result=vi.pipeline_proposals(self.root)[0]
        self.assertEqual(result['status'],'enabled');self.assertTrue(result['pipeline_enabled'])
        evidence.write_text('changed')
        self.assertEqual(vi.pipeline_proposals(self.root)[0]['status'],'approved_for_implementation')

    def test_concurrent_inbox_writer_fails_without_acknowledging_sources(self):
        with patch.object(vi,'sources',return_value=({'a'*64:self.source},[])):
            with vi.file_lock(self.root/'.routing.lock'):
                with self.assertRaisesRegex(RuntimeError,'another process'):
                    vi.apply(self.root,self.proposal())
            self.assertEqual(len(vi.prepare(self.root)['sources']),1)
            self.assertEqual(vi.ledger(self.root)['entries'],{})

    def test_receipt_needs_hash_and_manifest(self):
        data=self.root/'data/control-loop';item={'event_id':'one','sources':[{'audio_path':'/new.m4a'}]}
        envelope={'payload':{'item':item}};ref={'artifact_type':'voice_capture','artifact_id':'one','sha256':vi.digest(envelope)}
        vi.write(data/'artifacts/voice_capture/one.json',envelope)
        vi.write(data/'manifests/m.json',{'operation':'voice_intake','outputs':[ref]})
        response={'receipt':ref,'manifest_id':'m'}
        self.assertTrue(vi.verify_receipt({'data_root':str(self.root/'data')},response,item))
        with self.assertRaisesRegex(ValueError,'receipt_missing'):
            vi.verify_receipt({'data_root':str(self.root/'data')},{'manifest_id':'m'},item)
        vi.write(data/'manifests/m.json',{'operation':'voice_intake','outputs':[]})
        with self.assertRaisesRegex(ValueError,'receipt_payload'):vi.verify_receipt({'data_root':str(self.root/'data')},response,item)

if __name__=='__main__':unittest.main()
