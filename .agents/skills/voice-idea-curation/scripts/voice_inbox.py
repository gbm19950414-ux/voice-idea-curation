#!/usr/bin/env python3
"""Rule-based intake CLI. Codex supplies semantic proposals; scripts verify and deliver."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from asr_support import file_lock


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def stable_id(prefix,value):return f'{prefix}_{digest(value)[:24]}'
def read(path):return json.loads(Path(path).read_text())
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False,encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno());temp=f.name
    os.replace(temp,path)

def configuration(inbox):
    config=read(inbox/'routing/config.json');rules=read(inbox/'routing/rules.json')
    if config.get('schema_version')!='voice-routing-config/v1' or rules.get('schema_version')!='voice-routing/v1':
        raise ValueError('unsupported_routing_configuration')
    ids=[r['route_id'] for r in rules['rules']]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate_route_id')
    return config,rules

def audio_time(path):
    try:
        r=subprocess.run(['/opt/homebrew/bin/ffprobe','-v','error','-show_entries','format_tags=creation_time','-of','json',str(path)],capture_output=True,text=True,timeout=15,check=True)
        stamp=json.loads(r.stdout).get('format',{}).get('tags',{}).get('creation_time')
        if not stamp:return None
        dt=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if dt.tzinfo is None or dt.year<2020:return None
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    except (OSError,ValueError,subprocess.SubprocessError):return None

def sources(inbox):
    result={};problems=[]
    for manifest in sorted((inbox/'batches').glob('*/batch.json')):
        batch=read(manifest)
        if batch.get('schema_version')!=1 or batch.get('batch_id','').endswith('simulated'):
            continue
        if batch.get('status') in {'handed_off','ready_for_handoff'} or batch.get('workflow_status')=='ready_for_handoff':
            continue
        for source in batch.get('sources',[]):
            rid=source.get('id');sha=source.get('sha256')
            raw=next((r for r in reversed(batch.get('raw_transcriptions',[])) if r.get('id')==rid and r.get('source_sha256')==sha and r.get('status')=='success'),None)
            if raw is None:continue
            target=None
            for p in (manifest.parent/'raw').glob(f'{rid}.attempt-*.json'):
                if read(p)=={k:v for k,v in raw.items() if k!='attempt_path'}:target=p;break
            if target is None:
                problems.append({'recording_id':rid,'reason':'immutable_attempt_missing'});continue
            path=source.get('current_path') or source['source_path']
            value={'path':str(target.resolve()),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                   'locator':'/response/text','raw_text':raw['response']['text'],'audio_path':path,
                   'audio_sha256':sha,'recording_id':rid,'batch_id':batch['batch_id']}
            result[sha]=value
    return result,problems

def ledger(inbox):
    path=inbox/'routing/ledger.json'
    return read(path) if path.exists() else {'schema_version':'voice-routing-ledger/v1','entries':{}}

def prepare(inbox):
    config,rules=configuration(inbox);found,problems=sources(inbox);state=ledger(inbox)
    covered=set(state.get('completed_sources', []))
    pending=[]
    for sha,src in found.items():
        if sha in covered:continue
        pending.append(src)
    return {'schema_version':'voice-routing-input/v1','rules_sha256':digest(rules),'rules':rules,
            'sources':pending,'source_issues':problems,'instructions':'Supply source-linked semantic items; no fabricated human confirmation. Existing approved/handed-off batches are not reopened.'}

def project_interface(project):
    root=Path(project['path']).resolve();python=root/project.get('interpreter','.venv/bin/python')
    interface=root/'project-interface.yaml'
    raw=interface.read_text()
    try:doc=json.loads(raw)
    except json.JSONDecodeError:
        r=subprocess.run([str(python),'-c','import json,yaml,sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))',str(interface)],capture_output=True,text=True,timeout=30,check=True)
        doc=json.loads(r.stdout)
    if doc['project_id']!=project['project_id'] or doc.get('schema_version')!=2:
        raise ValueError('project_interface_identity_mismatch')
    entry=(root/doc['consumer']['entrypoint']).resolve()
    if not entry.is_relative_to(root) or not entry.is_file():raise ValueError('project_consumer_path_invalid')
    return doc,entry,python,hashlib.sha256(interface.read_bytes()).hexdigest()

def contained_file(root,relative):
    if not isinstance(relative,str) or not relative or Path(relative).is_absolute():raise ValueError('target_path_invalid')
    root=Path(root).resolve();path=(root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('target_path_invalid')
    return path

def target_version(project,doc,entry):
    root=Path(project['path']).resolve()
    names=doc.get('consumer',{}).get('version_files') or [str(entry.relative_to(root)),'project-interface.yaml']
    if not isinstance(names,list) or not names or any(not isinstance(name,str) for name in names):raise ValueError('target_version_files_invalid')
    hashes={name:hashlib.sha256(contained_file(root,name).read_bytes()).hexdigest() for name in names}
    repo=subprocess.run(['git','-C',str(root),'rev-parse','--show-toplevel'],capture_output=True,text=True)
    commit=None
    if repo.returncode==0 and Path(repo.stdout.strip()).resolve()==root:
        head=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True)
        if head.returncode==0:commit=head.stdout.strip()
    return commit,hashes,digest(hashes)

def verify_receipt(project,response,expected_item,interface=None):
    ref=response.get('receipt')
    if not isinstance(ref,dict):raise ValueError('target_receipt_missing')
    if interface and interface.get('consumer',{}).get('receipt_format')=='voice-delivery-receipt/v2':
        if ref.get('schema_version')!='voice-delivery-receipt/v2' or ref.get('event_id')!=expected_item['event_id']:
            raise ValueError('target_receipt_identity_mismatch')
        root=Path(project['data_root']).resolve()
        aref=ref.get('artifact');mref=ref.get('manifest')
        if not isinstance(aref,dict) or not isinstance(mref,dict):raise ValueError('target_receipt_missing')
        artifact_path=contained_file(root,aref.get('path'))
        manifest_path=contained_file(root,mref.get('path'))
        if hashlib.sha256(artifact_path.read_bytes()).hexdigest()!=aref.get('sha256') or hashlib.sha256(manifest_path.read_bytes()).hexdigest()!=mref.get('sha256'):
            raise ValueError('target_receipt_hash_mismatch')
        artifact=read(artifact_path);manifest=read(manifest_path)
        event_id=expected_item['event_id'];demand_id=response.get('demand_id')
        if response.get('event_id')!=event_id:
            raise ValueError('target_receipt_identity_mismatch')
        artifact_schema=interface.get('consumer',{}).get('artifact_schema','voice-delivery-artifact/v2')
        if artifact.get('schema_version')!=artifact_schema or artifact.get('event_id')!=event_id:
            raise ValueError('target_receipt_identity_mismatch')
        if 'demand_id' in response and (not isinstance(demand_id,str) or not demand_id or artifact.get('demand_id')!=demand_id or manifest.get('demand_id')!=demand_id):
            raise ValueError('target_receipt_identity_mismatch')
        if artifact.get('demand_id')!=manifest.get('demand_id'):
            raise ValueError('target_receipt_identity_mismatch')
        if normal_item(artifact.get('item'))!=normal_item(expected_item):raise ValueError('target_receipt_payload_mismatch')
        if manifest.get('schema_version')!='voice-delivery-manifest/v2' or manifest.get('operation')!='voice_intake' or manifest.get('event_id')!=event_id or manifest.get('artifact')!=aref:
            raise ValueError('target_receipt_manifest_mismatch')
        return True
    root=Path(project['data_root'])/'control-loop'
    p=contained_file(root,f"artifacts/{ref['artifact_type']}/{ref['artifact_id']}.json")
    m=contained_file(root,f"manifests/{response['manifest_id']}.json")
    artifact=read(p);manifest=read(m)
    if ref['artifact_id']!=expected_item['event_id'] or digest(artifact)!=ref['sha256']:
        raise ValueError('target_receipt_hash_mismatch')
    if normal_item(artifact['payload'].get('item'))!=normal_item(expected_item) or ref not in manifest['outputs']:
        raise ValueError('target_receipt_payload_mismatch')
    if manifest.get('operation')!='voice_intake':raise ValueError('target_receipt_operation_mismatch')
    return True

def normal_item(value):
    from copy import deepcopy
    value=deepcopy(value)
    if not isinstance(value,dict) or not isinstance(value.get('sources'),list):return value
    for source in value['sources']:
        if isinstance(source,dict):source.pop('audio_path',None)
    return value

def queued_source(entry):
    source=entry['source'];draft=entry['draft']
    if draft.get('source_sha256')!=source.get('audio_sha256') or draft.get('quote') not in source.get('raw_text',''):
        raise ValueError('queued_source_identity_mismatch')
    path=Path(source['path'])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:
        raise ValueError('queued_raw_hash_mismatch')
    raw=read(path)
    if source.get('locator')!='/response/text' or raw.get('response',{}).get('text')!=source['raw_text']:
        raise ValueError('queued_raw_text_mismatch')
    return source

def prepare_queued(inbox,event_id):
    _,rules=configuration(inbox);entry=ledger(inbox)['entries'].get(event_id)
    if not entry or entry.get('target_project') or entry.get('status')!='pending_pipeline':raise ValueError('queued_event_not_eligible')
    source=queued_source(entry)
    return {'schema_version':'voice-routing-queued-input/v1','event_id':event_id,'source':source,'draft':entry['draft'],
            'rules_sha256':digest(rules),'rules':rules,'instructions':'Replay this exact queued item with replaces_queued_event_id. Preserve source hash, quote and item_key.'}

def demand_handoffs(config,state,event_ids):
    """Derive queue handoff progress; a received demand is not an enabled pipeline."""
    refs=[];errors=[]
    for source_event in event_ids:
        entry=state['entries'].get(source_event);seen=set()
        while entry and entry.get('status')=='superseded':
            ident=entry.get('event_id')
            if ident in seen:
                entry=None;break
            seen.add(ident);entry=state['entries'].get(entry.get('superseded_by'))
        if not entry or entry.get('status')!='archived' or entry.get('delivery',{}).get('operation')!='demand':continue
        response=entry.get('receipt') or {}
        if not response.get('demand_id'):continue
        try:
            project={**config['projects'][entry['target_project']],'project_id':entry['target_project']}
            doc,_,_,_=project_interface(project)
            verify_receipt(project,response,entry['delivery'],doc)
            refs.append({'source_event_id':source_event,'event_id':entry['event_id'],
                         'project_id':entry['target_project'],'demand_id':response['demand_id']})
        except (ValueError,KeyError,OSError,subprocess.SubprocessError) as exc:
            errors.append({'source_event_id':source_event,'reason':str(exc)[:500]})
    return refs,errors

def pipeline_proposals(inbox):
    decisions=[read(p) for p in (inbox/'routing/decisions').glob('*.json')]
    config,rules=configuration(inbox)
    state=ledger(inbox)
    output=[]
    for path in sorted((inbox/'routing/proposals').glob('*.json')):
        doc=read(path)
        if doc.get('schema_version')!='voice-pipeline-proposal/v1':continue
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        matched=sorted([d for d in decisions if d.get('decision',{}).get('proposal_sha256')==sha],key=lambda d:d.get('known_at',''))
        decision=matched[-1] if matched else None
        scope=decision.get('decision',{}).get('scope') if decision else None
        enabled=False
        proof=inbox/'routing/enablements'/f"{doc['proposal_id']}.json"
        if decision and decision.get('status')=='approved_for_implementation' and proof.exists():
            claim=read(proof)
            evidence_ok=all(Path(ref['path']).is_file() and hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()==ref['sha256']
                            for ref in claim.get('evidence',[]))
            if claim.get('schema_version')=='voice-proposal-implementation/v1' and claim.get('proposal_sha256')==sha and claim.get('scope')==scope and evidence_ok and claim.get('evidence'):
                if scope=='new_pipeline':
                    route=next((r for r in rules['rules'] if r['route_id']==claim.get('route_id')),None)
                    replay=state['entries'].get(claim.get('replay_event_id'))
                    enabled=bool(route and route.get('approved_proposal_sha256')==sha and replay and replay.get('status')=='archived' and replay.get('route_id')==route['route_id'])
                elif scope=='project_improvement':
                    config,_=configuration(inbox)
                    project=config['projects'].get(doc.get('candidate_project')) or {}
                    receipt=claim.get('revision_receipt') or {}
                    artifact=Path(project.get('data_root','/missing'))/'control-loop/artifacts/voice_revision_draft'/f"{receipt.get('artifact_id')}.json"
                    enabled=bool(artifact.is_file() and receipt.get('artifact_type')=='voice_revision_draft' and digest(read(artifact))==receipt.get('sha256'))
        pending_status='needs_destination_or_intent' if doc.get('status')=='needs_destination_or_intent' else 'awaiting_specific_owner_approval'
        result={'proposal_id':doc['proposal_id'],'title':doc.get('title'), 'path':str(path),'sha256':sha,
                'status':('enabled' if scope=='new_pipeline' else 'implemented') if enabled else (decision['status'] if decision else pending_status),
                'scope':scope,'pipeline_enabled':bool(enabled and scope=='new_pipeline'),'candidate_project':doc.get('candidate_project')}
        if not decision and doc.get('status')=='needs_destination_or_intent':
            affected=list(dict.fromkeys(doc.get('affected_items',[])))
            refs,errors=demand_handoffs(config,state,affected)
            if refs:
                result['proposal_status']=result['status']
                result['status']='handed_to_demand' if len(refs)==len(affected) else 'partially_handed_to_demand'
                result['demand_refs']=refs
            if errors:result['handoff_errors']=errors
        output.append(result)
    return output

def status(inbox):
    state=ledger(inbox);groups={};counts={}
    for entry in state['entries'].values():
        counts[entry['status']]=counts.get(entry['status'],0)+1
        if entry['status']=='superseded':continue
        draft=entry['draft'];topic=draft.get('issue_key') or draft.get('pipeline_hint') or draft.get('exercise') or draft['nature']
        key=(entry.get('target_project') or 'pending_pipeline')+'::'+topic
        group=groups.setdefault(key,{'project':entry.get('target_project'),'topic':topic,'event_ids':[],'pending_questions':[],'summaries':[],'demand_refs':[]})
        group['event_ids'].append(entry['event_id']);group['summaries'].append(draft['ai_draft'])
        group['pending_questions'] += draft.get('questions',[])
        group['pending_questions'] += entry.get('receipt',{}).get('questions',[])
        receipt=entry.get('receipt') or {}
        if entry.get('target_project')=='life-projects' and receipt.get('demand_id'):
            group['demand_refs'].append({'event_id':entry['event_id'],'demand_id':receipt['demand_id'],'receipt':receipt.get('receipt')})
    for group in groups.values(): group['pending_questions']=sorted(set(group['pending_questions']))
    return {'pipeline_proposals': pipeline_proposals(inbox), 'schema_version':'voice-routing-status/v1','entries':len(state['entries']),'states':counts,'groups':list(groups.values()),'completed_sources':len(state.get('completed_sources',[])), 'failures': [r for r in read(inbox/'routing/last-result.json').get('results',[]) if r['status']=='rejected'] if (inbox/'routing/last-result.json').exists() else []}

def apply(inbox,proposal):
    config,rules=configuration(inbox)
    if proposal.get('schema_version')!='voice-routing-proposal/v1' or proposal.get('rules_sha256')!=digest(rules):
        raise ValueError('rules_version_mismatch')
    by_route={r['route_id']:r for r in rules['rules']};found,problems=sources(inbox)
    root=inbox/'routing';state=ledger(inbox);outputs=[]
    with file_lock(inbox/'.routing.lock'):
        state=ledger(inbox)
        for draft in proposal['items']:
            try:
                if any(k in draft for k in ['human_confirmed','status','confirmation_evidence']):raise ValueError('proposal_cannot_claim_human_confirmation')
                route=by_route[draft['route_id']]
                replacement=draft.get('replaces_queued_event_id')
                prior_queued=state['entries'].get(replacement) if replacement else None
                if replacement:
                    if not prior_queued or prior_queued.get('target_project') or prior_queued.get('status') not in {'pending_pipeline','superseded'}:
                        raise ValueError('queued_replacement_source_mismatch')
                    source=queued_source(prior_queued)
                    if (draft['source_sha256']!=source['audio_sha256'] or
                        draft['quote']!=prior_queued['draft']['quote'] or
                        draft['item_key']!=prior_queued['draft']['item_key']):
                        raise ValueError('queued_replacement_identity_mismatch')
                else:source=found[draft['source_sha256']]
                if draft['quote'] not in source['raw_text']:raise ValueError('quote_not_in_source')
                if draft['nature'] not in route['natures']:raise ValueError('route_nature_mismatch')
                if route['operation'] == 'measurement' and source.get('captured_at') is None:
                    stamp=audio_time(source['audio_path'])
                    if stamp:source={**source,'captured_at':stamp}
                identity=stable_id('voice',{'sources':[(source['audio_sha256'],source['sha256'])],
                                          'quote':draft['quote'],'item_key':draft['item_key'],'operation':route['operation']})
                if route.get('project_id') is None:
                    value={'route_id':draft['route_id'],'source':source,'draft':draft,'status':'pending_pipeline','event_id':identity,'rules_sha256':digest(rules)}
                    previous=state['entries'].get(identity)
                    if previous and previous['draft']!=draft:raise ValueError('queued_item_payload_conflict')
                    if previous and previous.get('status')=='superseded':
                        value['status']='superseded';value['superseded_by']=previous['superseded_by']
                    state['entries'][identity]=value
                    outputs.append({'event_id':identity,'status':'duplicate' if previous else 'queued','route_id':draft['route_id'],'source_sha256':draft['source_sha256']})
                    write(root/'ledger.json',state)
                    continue
                if replacement and prior_queued.get('status')=='superseded' and prior_queued.get('superseded_by')!=identity:
                    raise ValueError('queued_replacement_already_superseded')
                project={**config['projects'][route['project_id']],'project_id':route['project_id']}
                doc,entry,python,interface_hash=project_interface(project)
                if doc.get('capabilities',{}).get(route['operation'])!='voice-delivery/v1':raise ValueError('target_capability_unavailable')
                item={'event_id':identity,'item_key':draft['item_key'],'project_id':route['project_id'],
                      'operation':route['operation'],'sources':[source],'quote':draft['quote'],
                      'ai_draft':draft['ai_draft'],'nature':draft['nature'],'questions':draft.get('questions',[])}
                for key in ['exercise','issue_key','measurement','meal_log','revision','proposal','supersedes_event_id','human_confirmation','correction_evidence','session_ref','demand']:
                    if key in draft:item[key]=draft[key]
                if route['operation']=='meal_log' and route.get('meal_convention_evidence'):
                    item['meal_log']={**item['meal_log'], 'convention_evidence':route['meal_convention_evidence']}
                if item.get('measurement'):
                    item['measurement'].setdefault('subject_id',config.get('subject_id'))
                    # Named third-party reports are outside configured personal attribution.
                    import re
                    if re.search(r'(朋友|别人|爸爸|妈妈|客户|学员|他|她)(?:的)?[^。！？；\n]{0,12}体重',source['raw_text']):
                        item['measurement']['subject_id']=None
                old=state['entries'].get(identity)
                if old and old.get('delivery') and normal_item(old['delivery'])!=normal_item(item):raise ValueError('routed_item_payload_conflict')
                version,code_hashes,content_hash=target_version(project,doc,entry)
                payload_file=root/'deliveries'/f'{identity}.json';write(payload_file,[item])
                r=subprocess.run([str(python),str(entry),'--data-root',project['data_root'],'--json','receive','--input',str(payload_file)],capture_output=True,text=True,timeout=90)
                if r.returncode:raise ValueError('target_failed: '+r.stderr[-600:])
                response=json.loads(r.stdout)[0]
                if response['status'] not in {'accepted','duplicate'}:
                    outputs.append({**response,'route_id':draft['route_id'],'source_sha256':draft['source_sha256']});continue
                verify_receipt(project,response,item,doc)
                # Target commit and content hash are consumer evidence, not event identity.
                transport_response = response
                if old and old.get('receipt'):
                    response = old['receipt']
                state['entries'][identity]={'event_id':identity,'route_id':draft['route_id'],'source':source,
                    'delivery':item,'draft':draft,'status':old['status'] if old and old['status']=='superseded' else response['archive_status'],'receipt':response,
                    'producer_code_hashes': old.get('producer_code_hashes', 'unknown') if old else code_hashes,
                    'target_project':route['project_id'],'target_commit': old.get('target_commit', version) if old else version,
                    'target_content_sha256':old.get('target_content_sha256',content_hash) if old else content_hash,
                    'interface_sha256':interface_hash,'rules_sha256':digest(rules)}
                if old and old.get('superseded_by'): state['entries'][identity]['superseded_by']=old['superseded_by']
                supersedes=item.get('supersedes_event_id') or draft.get('replaces_queued_event_id')
                if supersedes in state['entries']:
                    state['entries'][supersedes]['status']='superseded'
                    state['entries'][supersedes]['superseded_by']=identity
                write(root/'ledger.json',state)
                outputs.append({**transport_response,'route_id':draft['route_id'],'source_sha256':draft['source_sha256']})
            except (ValueError,KeyError,OSError,subprocess.SubprocessError) as exc:
                outputs.append({'item_key':draft.get('item_key'),'status':'rejected','reason':str(exc)[:1200],'source_sha256':draft.get('source_sha256')})
        completed=set(state.get('completed_sources',[]))
        for sha in {d['source_sha256'] for d in proposal['items']}:
            results=[o for o in outputs if o.get('source_sha256')==sha]
            if results and all(o['status'] in {'accepted','duplicate','queued'} for o in results): completed.add(sha)
            elif not any(d.get('source_sha256')==sha and d.get('replaces_queued_event_id') for d in proposal['items']):completed.discard(sha)
        state['completed_sources']=sorted(completed);write(root/'ledger.json',state)
        signal={'entries':[{k:v for k,v in e.items() if k not in ['target_commit', 'target_content_sha256', 'producer_code_hashes']} for e in state['entries'].values()],'source_issues':problems,'rejections': [o for o in outputs if o['status']=='rejected']}
        fingerprint=digest(signal);previous=read(root/'last-result.json') if (root/'last-result.json').exists() else {}
        result={'schema_version':'voice-routing-result/v1','results':outputs,'source_issues':problems,
                'actionable_change':fingerprint!=previous.get('fingerprint'),'fingerprint':fingerprint,'scheduled_trigger_verified':False}
        if not outputs and not state['entries'] and not problems: result['actionable_change'] = False
        if outputs and all(o['status']=='duplicate' for o in outputs) and problems==previous.get('source_issues',[]) and not any(o.get('status')=='rejected' for o in previous.get('results',[])):
            result['actionable_change']=False
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        write(root/'runs'/f'{stamp}.json',result);write(root/'last-result.json',result)
        return result

def record_decision(inbox, decision):
    if decision.get('scope') not in {'new_pipeline','project_improvement'} or decision.get('action') not in {'approve','reject'}:
        raise ValueError('unsupported_proposal_decision')
    path=Path(decision['proposal_path']).resolve()
    if not path.is_relative_to((inbox/'routing/proposals').resolve()):raise ValueError('proposal_outside_private_registry')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=decision['proposal_sha256']:raise ValueError('proposal_version_mismatch')
    proposal=read(path)
    if proposal.get('schema_version')!='voice-pipeline-proposal/v1':raise ValueError('pipeline_proposal_required')
    evidence=decision['evidence'];epath=Path(evidence['path']);raw=epath.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=evidence['sha256']:raise ValueError('decision_evidence_hash_mismatch')
    owner=json.loads(raw)
    if owner.get('kind')!='user_message' or not owner.get('text') or not owner.get('message_ref'):
        raise ValueError('owner_decision_evidence_required')
    if datetime.fromisoformat(owner['known_at'].replace('Z','+00:00')).tzinfo is None:raise ValueError('owner_decision_time_required')
    if any(owner.get(k)!=decision[k] for k in ['proposal_sha256','action','scope']):raise ValueError('owner_decision_target_mismatch')
    if owner.get('proposal_id')!=proposal['proposal_id']:raise ValueError('owner_decision_id_mismatch')
    identity=stable_id('proposal_decision',decision);out=inbox/'routing/decisions'/f'{identity}.json'
    with file_lock(inbox/'.routing.lock'):
        exists=out.exists()
        value={'schema_version':'voice-proposal-decision/v1','proposal_id':proposal['proposal_id'],'decision':decision,
               'status':'approved_for_implementation' if decision['action']=='approve' else 'rejected',
               'pipeline_enabled':False,'known_at':owner['known_at']}
        if exists and read(out)!=value:raise ValueError('decision_payload_conflict')
        if not exists:write(out,value)
    return {'status':'duplicate' if exists else 'recorded','decision_id':identity,'path':str(out),'pipeline_enabled':False}

def show(inbox,args):
    config,_=configuration(inbox)
    project={**config['projects'][args.project],'project_id':args.project}
    _,entry,python,_=project_interface(project)
    cmd=[str(python),str(entry),'--data-root',project['data_root']]
    if args.json:cmd+=['--json']
    cmd+=['show']
    for key in ['exercise','issue','as_of']:
        value=getattr(args,key,None)
        if value:cmd+=['--'+key.replace('_','-'),value]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    if r.returncode:raise ValueError(r.stderr)
    return r.stdout

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inbox',type=Path,required=True);p.add_argument('--json',action='store_true')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('prepare');sub.add_parser('status')
    a=sub.add_parser('prepare-queued');a.add_argument('--event-id',required=True)
    a=sub.add_parser('apply');a.add_argument('--proposal',type=Path,required=True);a.add_argument('--quiet-if-unchanged',action='store_true')
    a=sub.add_parser('scan');a.add_argument('--batch-id')
    a=sub.add_parser('show');a.add_argument('--project',default='body_building');a.add_argument('--exercise');a.add_argument('--issue');a.add_argument('--as-of')
    a=sub.add_parser('decide');a.add_argument('--project',default='body_building');a.add_argument('--input',type=Path,required=True)
    a=sub.add_parser('revise');a.add_argument('--project',default='body_building');a.add_argument('--input',type=Path,required=True)
    args=p.parse_args();inbox=args.inbox.resolve()
    try:
        if args.command=='prepare':value=prepare(inbox)
        elif args.command=='prepare-queued':value=prepare_queued(inbox,args.event_id)
        elif args.command=='apply':
            value=apply(inbox,read(args.proposal))
            if args.quiet_if_unchanged and not value['actionable_change']:return 0
        elif args.command=='status':
            value=status(inbox)
        elif args.command=='show':print(show(inbox,args),end='');return 0
        elif args.command=='revise':
            config,_=configuration(inbox);project={**config['projects'][args.project],'project_id':args.project}
            _,entry,python,_=project_interface(project)
            r=subprocess.run([str(python),str(entry),'--data-root',project['data_root'],'--json','revise','--input',str(args.input)],capture_output=True,text=True,timeout=60)
            if r.returncode:raise ValueError(r.stderr)
            print(r.stdout,end='');return 0
        elif args.command=='decide':
            decision=read(args.input)
            if decision.get('scope'):
                print(json.dumps(record_decision(inbox,decision),ensure_ascii=False,indent=2));return 0
            config,_=configuration(inbox);project={**config['projects'][args.project],'project_id':args.project}
            _,entry,python,_=project_interface(project)
            r=subprocess.run([str(python),str(entry),'--data-root',project['data_root'],'--json','decide','--input',str(args.input)],capture_output=True,text=True,timeout=60)
            if r.returncode:raise ValueError(r.stderr)
            print(r.stdout,end='');return 0
        else:
            config,_=configuration(inbox);folder=Path(__file__).parent
            cmd=[sys.executable,str(folder/'scan_and_transcribe.py'),config['source_directory'],str(inbox),'--transcriber',str(folder/'local_asr.py'),'--transcriber-config',config['transcriber_config'],'--skill-version',subprocess.check_output(['git','-C',str(folder),'rev-parse','HEAD'],text=True).strip()]
            if args.batch_id:cmd+=['--batch-id',args.batch_id]
            r=subprocess.run(cmd,check=True);return r.returncode
        print(json.dumps(value,ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,RuntimeError,subprocess.SubprocessError) as exc:p.error(str(exc))
    return 0
if __name__=='__main__':raise SystemExit(main())
