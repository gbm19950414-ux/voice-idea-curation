import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import voice_inbox as vi


CONSUMER = '''import argparse, hashlib, json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--data-root');p.add_argument('--json',action='store_true');p.add_argument('command');p.add_argument('--input');a=p.parse_args()
root=Path(a.data_root);root.mkdir(parents=True,exist_ok=True)
item=json.loads(Path(a.input).read_text())[0]
event=item['event_id'];demand='DEMAND-'+event[-8:]
mode=(root/'mode.txt').read_text() if (root/'mode.txt').exists() else ''
generic=mode=='generic'
artifact={'schema_version':'voice-delivery-artifact/v2' if generic else 'life-voice-intake/v1','event_id':event,'item':item}
if not generic:artifact['demand_id']=demand
ap=root/'intake'/f'{event}.json';ap.parent.mkdir(parents=True,exist_ok=True);existed=ap.exists()
ap.write_text(json.dumps(artifact,ensure_ascii=False))
aref={'path':str(ap.relative_to(root)),'sha256':hashlib.sha256(ap.read_bytes()).hexdigest()}
manifest={'schema_version':'voice-delivery-manifest/v2','operation':'voice_intake','event_id':event,'artifact':aref}
if not generic:manifest['demand_id']=demand
mp=root/'receipts'/f'{event}.json';mp.parent.mkdir(parents=True,exist_ok=True)
mp.write_text(json.dumps(manifest,ensure_ascii=False))
receipt={'schema_version':'voice-delivery-receipt/v2','event_id':event,'artifact':aref,'manifest':{'path':str(mp.relative_to(root)),'sha256':hashlib.sha256(mp.read_bytes()).hexdigest()}}
if mode=='missing':receipt=None
response={'status':'duplicate' if existed else 'accepted','archive_status':'archived','event_id':event,'receipt':receipt}
if not generic:response['demand_id']=demand
print(json.dumps([response]))
'''


class LifeIntakeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.inbox=self.root/'inbox';self.project=self.root/'life';self.project.mkdir()
        (self.project/'consumer.py').write_text(CONSUMER)
        interface={'schema_version':2,'project_id':'life-projects','capabilities':{'demand':'voice-delivery/v1'},
                   'consumer':{'entrypoint':'consumer.py','receipt_format':'voice-delivery-receipt/v2',
                               'artifact_schema':'life-voice-intake/v1',
                               'version_files':['consumer.py','project-interface.yaml']}}
        vi.write(self.project/'project-interface.yaml',interface)
        self.config={'schema_version':'voice-routing-config/v1','projects':{
            'life-projects':{'path':str(self.project),'interpreter':sys.executable,'data_root':str(self.root/'data')}}}
        self.rules={'schema_version':'voice-routing/v1','rules':[
            {'route_id':'queue','project_id':None,'operation':'queued','natures':['other_project']},
            {'route_id':'life','project_id':'life-projects','operation':'demand','natures':['other_project']}]}
        vi.write(self.inbox/'routing/config.json',self.config);vi.write(self.inbox/'routing/rules.json',self.rules)
        raw=self.root/'raw.json';vi.write(raw,{'response':{'text':'实验试剂用完了。'}})
        self.source={'path':str(raw),'sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),
                     'locator':'/response/text','raw_text':'实验试剂用完了。','audio_path':'/source.m4a',
                     'audio_sha256':'a'*64,'recording_id':'r1','batch_id':'b1'}

    def proposal(self,route='queue',**extra):
        draft={'route_id':route,'source_sha256':self.source['audio_sha256'],'quote':'实验试剂用完了。',
               'item_key':'reagent','ai_draft':'记录试剂需求','nature':'other_project',**extra}
        return {'schema_version':'voice-routing-proposal/v1','rules_sha256':vi.digest(self.rules),'items':[draft]}

    def queue(self):
        with patch.object(vi,'sources',return_value=({self.source['audio_sha256']:self.source},[])):
            result=vi.apply(self.inbox,self.proposal())
        return result['results'][0]['event_id']

    def replay(self,queued,**extra):
        with patch.object(vi,'sources',return_value=({},[])):
            return vi.apply(self.inbox,self.proposal('life',replaces_queued_event_id=queued,
                demand={'title':'补充实验试剂','outcome':'继续实验'},**extra))

    def test_replay_uses_immutable_queue_and_non_git_consumer(self):
        queued=self.queue()
        prepared=vi.prepare_queued(self.inbox,queued)
        self.assertEqual(prepared['source'],self.source)
        first=self.replay(queued)
        self.assertEqual(first['results'][0]['status'],'accepted')
        delivered=next(e for e in vi.ledger(self.inbox)['entries'].values() if e.get('target_project'))
        self.assertEqual(delivered['delivery']['demand']['title'],'补充实验试剂')
        self.assertIsNone(delivered['target_commit'])
        self.assertEqual(set(delivered['producer_code_hashes']),{'consumer.py','project-interface.yaml'})
        self.assertEqual(vi.ledger(self.inbox)['entries'][queued]['status'],'superseded')
        second=self.replay(queued)
        self.assertEqual(second['results'][0]['status'],'duplicate')
        self.assertEqual(len(vi.ledger(self.inbox)['entries']),2)
        refs=vi.status(self.inbox)['groups'][0]['demand_refs']
        self.assertEqual(refs[0]['demand_id'],delivered['receipt']['demand_id'])

    def test_generic_v2_target_without_demand_id(self):
        interface=vi.read(self.project/'project-interface.yaml')
        interface['project_id']='generic-project'
        interface['consumer'].pop('artifact_schema')
        vi.write(self.project/'project-interface.yaml',interface)
        self.config['projects']['generic-project']=self.config['projects'].pop('life-projects')
        self.rules['rules'][1]['project_id']='generic-project'
        vi.write(self.inbox/'routing/config.json',self.config)
        vi.write(self.inbox/'routing/rules.json',self.rules)
        (self.root/'data').mkdir();(self.root/'data/mode.txt').write_text('generic')
        queued=self.queue();result=self.replay(queued)
        self.assertEqual(result['results'][0]['status'],'accepted')
        self.assertNotIn('demand_id',result['results'][0])
        delivered=next(e for e in vi.ledger(self.inbox)['entries'].values() if e.get('target_project'))
        self.assertEqual(delivered['target_project'],'generic-project')

    def test_old_pipeline_candidate_tracks_receipted_handoff_without_enablement(self):
        queued=self.queue()
        path=self.inbox/'routing/proposals/pipeline-example.json'
        proposal={'schema_version':'voice-pipeline-proposal/v1','proposal_id':'example',
                  'title':'待讨论管道','status':'needs_destination_or_intent','affected_items':[queued]}
        vi.write(path,proposal);before=path.read_bytes()
        self.assertEqual(vi.pipeline_proposals(self.inbox)[0]['status'],'needs_destination_or_intent')
        self.replay(queued)
        status=vi.pipeline_proposals(self.inbox)[0]
        self.assertEqual(status['status'],'handed_to_demand')
        self.assertFalse(status['pipeline_enabled'])
        self.assertEqual(status['demand_refs'][0]['source_event_id'],queued)
        self.assertEqual(path.read_bytes(),before)
        delivered=next(e for e in vi.ledger(self.inbox)['entries'].values() if e.get('target_project'))
        (self.root/'data'/delivered['receipt']['receipt']['manifest']['path']).unlink()
        broken=vi.pipeline_proposals(self.inbox)[0]
        self.assertEqual(broken['status'],'needs_destination_or_intent')
        self.assertIn('target_path_invalid',broken['handoff_errors'][0]['reason'])

    def test_partial_candidate_handoff_does_not_claim_all_sources_received(self):
        queued=self.queue()
        vi.write(self.inbox/'routing/proposals/pipeline-example.json',{
            'schema_version':'voice-pipeline-proposal/v1','proposal_id':'example',
            'status':'needs_destination_or_intent','affected_items':[queued,'still-queued']})
        self.replay(queued)
        result=vi.pipeline_proposals(self.inbox)[0]
        self.assertEqual(result['status'],'partially_handed_to_demand')
        self.assertFalse(result['pipeline_enabled'])
        self.assertEqual(len(result['demand_refs']),1)

    def test_replay_rejects_changed_quote_key_and_raw(self):
        queued=self.queue()
        for change in ({'quote':'实验试剂'}, {'item_key':'other'}, {'source_sha256':'b'*64}):
            result=self.replay(queued,**change)
            self.assertEqual(result['results'][0]['status'],'rejected')
            self.assertIn('queued_replacement_identity_mismatch',result['results'][0]['reason'])
        raw=Path(self.source['path']);raw.write_text('{}')
        with self.assertRaisesRegex(ValueError,'queued_raw_hash_mismatch'):vi.prepare_queued(self.inbox,queued)
        self.assertIn('queued_raw_hash_mismatch',self.replay(queued)['results'][0]['reason'])

    def test_missing_receipt_rejected_and_retry_recovers(self):
        queued=self.queue();(self.root/'data').mkdir();(self.root/'data/mode.txt').write_text('missing')
        first=self.replay(queued)
        self.assertIn('target_receipt_missing',first['results'][0]['reason'])
        self.assertEqual(vi.ledger(self.inbox)['entries'][queued]['status'],'pending_pipeline')
        (self.root/'data/mode.txt').unlink()
        self.assertEqual(self.replay(queued)['results'][0]['status'],'duplicate')

    def test_v2_receipt_rejects_tamper_and_escape(self):
        queued=self.queue();self.replay(queued)
        delivered=next(e for e in vi.ledger(self.inbox)['entries'].values() if e.get('target_project'))
        item=delivered['delivery'];response=delivered['receipt'];project=self.config['projects']['life-projects']
        interface=vi.read(self.project/'project-interface.yaml')
        self.assertTrue(vi.verify_receipt(project,response,item,interface))
        escaped=json.loads(json.dumps(response));escaped['receipt']['artifact']['path']='../outside.json'
        with self.assertRaisesRegex(ValueError,'target_path_invalid'):vi.verify_receipt(project,escaped,item,interface)
        ap=self.root/'data'/response['receipt']['artifact']['path'];ap.write_text('{}')
        with self.assertRaisesRegex(ValueError,'target_receipt_hash_mismatch'):vi.verify_receipt(project,response,item,interface)

    def test_v2_receipt_checks_item_even_with_matching_hashes(self):
        queued=self.queue();self.replay(queued)
        delivered=next(e for e in vi.ledger(self.inbox)['entries'].values() if e.get('target_project'))
        item=delivered['delivery'];response=json.loads(json.dumps(delivered['receipt']))
        root=self.root/'data';ref=response['receipt'];ap=root/ref['artifact']['path'];mp=root/ref['manifest']['path']
        artifact=vi.read(ap);artifact['item']['quote']='different';vi.write(ap,artifact)
        ref['artifact']['sha256']=hashlib.sha256(ap.read_bytes()).hexdigest()
        manifest=vi.read(mp);manifest['artifact']=ref['artifact'];vi.write(mp,manifest)
        ref['manifest']['sha256']=hashlib.sha256(mp.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'target_receipt_payload_mismatch'):
            vi.verify_receipt(self.config['projects']['life-projects'],response,item,vi.read(self.project/'project-interface.yaml'))

    def test_declared_version_files_must_stay_under_project(self):
        interface=vi.read(self.project/'project-interface.yaml')
        interface['consumer']['version_files']=['../raw.json']
        vi.write(self.project/'project-interface.yaml',interface)
        queued=self.queue();result=self.replay(queued)
        self.assertEqual(result['results'][0]['status'],'rejected')
        self.assertIn('target_path_invalid',result['results'][0]['reason'])


if __name__=='__main__':unittest.main()
