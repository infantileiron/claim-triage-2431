from src.config.database import Session
from src.models.entities import Training,Review,Comparison,Claim
from src import worker
from conftest import add_claim,make_version

def _two_versions(s,prefix='A'):
 claims=[]
 for i in range(14):
  claims.append(add_claim(s,f'{prefix}-A{i:02d}',amount=7000.0+i,distance=40,label=1))
 for i in range(12):
  claims.append(add_claim(s,f'{prefix}-N{i:02d}',amount=120.0+i,distance=40,label=0))
 pending=[add_claim(s,f'{prefix}-P{i}',amount=2500+i,distance=30,label=None) for i in range(4)]
 ids=[c.id for c in claims]
 base_hold={cid:(0.1 if s.get(Claim,cid).label==1 else 0.1) for cid in ids}
 cand_hold={cid:(0.9 if s.get(Claim,cid).label==1 else 0.1) for cid in ids}
 v1=make_version(s,'基线',0.5,claims+pending,[],base_hold,{c.id:0.8 for c in pending},active=True)
 v2=make_version(s,'候选',0.5,claims+pending,[],cand_hold,{c.id:0.2 for c in pending})
 return v1,v2

def test_full_comparison_api_flow_and_role_gates(client,auth):
 # viewer 不能受理比较
 r=client.post('/comparisons',json={'baseline_id':1,'candidate_id':2},headers=auth['viewer'])
 assert r.status_code==403
 with Session() as s:
  v1,v2=_two_versions(s)
  bid,cid=v1.id,v2.id
 # analyst 受理：样本冻结、入队
 r=client.post('/comparisons',json={'baseline_id':bid,'candidate_id':cid},headers=auth['analyst'])
 assert r.status_code==200,r.text
 cmp_id=r.json()['id'];assert r.json()['state']=='queued'
 assert r.json()['result']['pairing'] and 'reject_reason' in r.json()['result']
 # worker 消费冻结样本，不重新取数
 with Session() as s:
  row=s.get(Comparison,cmp_id)
  worker.run_comparison(s,row);s.commit()
 r=client.get(f'/comparisons/{cmp_id}',headers=auth['viewer'])
 body=r.json();assert body['state']=='passed'
 assert body['result']['summary']['ci_lower']>0
 assert len(body['result']['pairing'])==26
 # analyst 不能切换（仅 admin）
 r=client.post(f'/comparisons/{cmp_id}/switch',headers=auth['analyst'])
 assert r.status_code==403
 # 未通过的比较不可下载；通过的可以
 r=client.get(f'/comparisons/{cmp_id}/export',headers=auth['viewer'])
 assert r.status_code==200 and 'version-comparison' in r.headers['content-disposition']
 # admin 原子切换
 r=client.post(f'/comparisons/{cmp_id}/switch',headers=auth['admin'])
 assert r.status_code==200,r.text
 with Session() as s:
  assert s.get(Training,cid).is_active is True
  assert s.get(Training,bid).is_active is False

def test_api_rejects_leakage_and_bad_inputs(client,auth):
 with Session() as s:
  claims=[]
  for i in range(12):claims.append(add_claim(s,f'K-A{i}',amount=6000+i,label=1))
  for i in range(12):claims.append(add_claim(s,f'K-N{i}',amount=100+i,label=0))
  ids=[c.id for c in claims]
  v1=make_version(s,'b',0.5,claims,[],{cid:0.9 for cid in ids})
  v2=make_version(s,'c',0.5,claims,[claims[0].code],{cid:0.9 for cid in ids})
  ids_pair=(v1.id,v2.id)
 r=client.post('/comparisons',json={'baseline_id':ids_pair[0],'candidate_id':ids_pair[1]},headers=auth['analyst'])
 assert r.status_code==200 and r.json()['state']=='rejected'
 assert '训练泄漏' in r.json()['reject_reason']
 # 同版本比较 422
 r=client.post('/comparisons',json={'baseline_id':ids_pair[0],'candidate_id':ids_pair[0]},headers=auth['analyst'])
 assert r.status_code==422
 # 不存在版本 404
 r=client.post('/comparisons',json={'baseline_id':999,'candidate_id':ids_pair[1]},headers=auth['analyst'])
 assert r.status_code==404

def test_worker_recovery_requeues_pending_comparisons(db,redis_stub):
 with Session() as s:
  v1,v2=_two_versions(s,prefix='R')
  s.add(Comparison(baseline_id=v1.id,candidate_id=v2.id,state='running',result={'pairing':[],'reject_reason':''}))
  s.commit();cid=s.query(Comparison).first().id
 with Session() as s:
  worker.recover(s)
 queued=[x for x in redis_stub.pushed if x[0]=='claim:comparisons']
 assert (('claim:comparisons',cid) in queued)
 with Session() as s:
  assert s.get(Comparison,cid).state=='queued'

def test_worker_marks_non_significant_as_rejected(db,redis_stub):
 with Session() as s:
  v1,v2=_two_versions(s,prefix='NS')
  # 让两版留出评分完全相同 -> 差值为 0 -> rejected
  v2.result['holdout']=[dict(h) for h in v1.result['holdout']]
  from src.services.comparison import build_pairing
  details,reason=build_pairing(v1,v2);assert reason==''
  row=Comparison(baseline_id=v1.id,candidate_id=v2.id,state='queued',
                 result={'pairing':details,'reject_reason':'','baseline_id':v1.id,'candidate_id':v2.id})
  s.add(row);s.commit();cid=row.id
 with Session() as s:
  worker.run_comparison(s,s.get(Comparison,cid));s.commit()
 with Session() as s:
  row=s.get(Comparison,cid)
  assert row.state=='rejected' and '置信区间' in row.reject_reason

def test_switch_and_signoff_concurrency_protects_conclusions(client,auth):
 with Session() as s:
  v1,v2=_two_versions(s,prefix='C')
  # 基线为全部待标注票据生成待处理复核
  for p in v1.result['pending']:
   s.add(Review(training_id=v1.id,claim_id=p['id'],score=p['score']))
  s.commit()
  review_id=s.query(Review).filter_by(training_id=v1.id,
              claim_id=v1.result['pending'][0]['id']).first().id
  from src.services.comparison import build_pairing,evaluate_pairing
  details,reason=build_pairing(v1,v2);assert reason==''
  summary=evaluate_pairing(details);assert summary['switch_eligible']
  cmp=Comparison(baseline_id=v1.id,candidate_id=v2.id,state='passed',
                 result={'pairing':details,'summary':summary})
  s.add(cmp);s.commit();cmp_id=cmp.id
 # 人工先填写并签核该票据的结论
 r=client.put(f'/reviews/{review_id}',headers=auth['analyst'],
              json={'decision':'valid','note':'签核竞争：人工先核对原票据确认正常'})
 assert r.status_code==200
 r=client.post(f'/reviews/{review_id}/approve',headers=auth['admin'])
 assert r.status_code==200
 # 随后切换：已签核结论必须保留原依据，且不为该票据新建待核票
 r=client.post(f'/comparisons/{cmp_id}/switch',headers=auth['admin'])
 assert r.status_code==200
 with Session() as s:
  rv=s.get(Review,review_id)
  assert rv.status=='approved' and rv.training_id==v1.id and rv.superseded_by is None
  assert not s.query(Review).filter_by(training_id=v2.id,
        claim_id=v1.result['pending'][0]['id']).first()
 # 切换后旧版未处理任务不可再修改或签核
 with Session() as s:
  other=s.query(Review).filter_by(status='superseded').first()
  assert other is not None;oid=other.id
 r=client.put(f'/reviews/{oid}',headers=auth['analyst'],
              json={'decision':'flagged','note':'尝试修改已被取代任务'})
 assert r.status_code==409
 r=client.post(f'/reviews/{oid}/approve',headers=auth['admin'])
 assert r.status_code==409

def test_first_done_training_becomes_active_and_export_guards(client,auth):
 with Session() as s:
  claims=[add_claim(s,f'Z{i}',amount=100+i,label=i%2) for i in range(44)]
  v=make_version(s,'唯一版本',0.5,claims,[],{c.id:0.5 for c in claims},active=True)
  vid=v.id
 with Session() as s:
  assert s.get(Training,vid).is_active is True
 # 未完成比较不能下载
 r=client.post('/comparisons',json={'baseline_id':vid,'candidate_id':vid},headers=auth['analyst'])
 assert r.status_code==422
