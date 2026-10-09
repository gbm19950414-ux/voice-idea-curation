"""Offline synthetic review example. No audio, user data or network."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.agents/skills/voice-idea-curation/scripts'))
from prepare_review import prepare
batch={"batch_id":"synthetic-demo","sources":[{"id":"demo-1","sha256":"synthetic-only","source_path":"synthetic://idea-1","status":"success"}],"raw_transcriptions":[{"id":"demo-1","status":"success","response":{"text":"我想把明天的主要行动缩短到三十分钟。"}}]}
curation={"recordings":[{"recording_id":"demo-1","summary":"尝试把主要行动缩短到30分钟","ai_draft":"我想把明天的主要行动缩短到30分钟。","uncertainties":[{"question":"这是只调整明天，还是长期安排？"}]}]}
review,markdown=prepare(batch,curation)
assert review['status']=='pending_human_review'
assert not review['recordings'][0]['confirmation_evidence']
out=ROOT/'demo-output';out.mkdir(exist_ok=True)
(out/'review.md').write_text(markdown,encoding='utf-8')
(out/'review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
print('Generated synthetic review; human confirmation remains pending.')
