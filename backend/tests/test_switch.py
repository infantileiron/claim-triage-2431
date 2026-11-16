from src.config.database import Session
from src.models.entities import Training,Review,Comparison
from src.services.switch import perform_switch,ensure_single_active
from conftest import add_claim,make_version

def _setup(s,prefix='W'):
 claims=[]
 # 24 异常（高金额）、20 正常（低金额），全部已标注，满足配对门槛
 for i in range(24):
  claims.append(add_claim(s,f'{prefix}-A{i:02d}',amount=5000.0+i,distance=40,label=1))
 for i in range(20):
  claims.append(add_claim(s,f'{prefix}-N{i:02d}',amount=150.0+i,distance=40,label=0))
 # 另加 6 笔待标注，用于切换后的复核清单重算
 pending=[]
 for i in range(6):
  pending.append(add_claim(s,f'{prefix}-P{i:02d}',amount=3000.0+i,distance=40,label=None))
 ids=[c.id for c in claims]
 base_hold={cid:(0.1 if s.get(type(claims[0]),cid).label==1 else 0.1) for cid in ids}
 cand_hold={cid:(0.9 if s.get(type(claims[0]),cid).label==1 else 0.1) for cid in ids}
 # 基线为全部待标注票据打分且过阈 -> 生成 6 条未处理复核
 base_pending={c.id:0.8 for c in pending}
 # 候选只让前 4 笔过阈
 cand_pending={c.id:(0.9 if i<4 else 0.2) for i,c in enumerate(pending)}
 v1=make_version(s,'基线',0.5,claims+pending,[],base_hold,base_pending,active=True)
 v2=make_version(s,'候选',0.5,claims+pending,[],cand_hold,cand_pending)
 return claims,pending,v1,v2

def _passed_comparison(s,v1,v2):
 from src.services.comparison import build_pairing,evaluate_pairing
 details,reason=build_pairing(v1,v2);assert reason==''
 summary=evaluate_pairing(details);assert summary['switch_eligible']
 row=Comparison(baseline_id=v1.id,candidate_id=v2.id,state='passed',reject_reason='',
                result={'pairing':details,'summary':summary,'baseline_id':v1.id,'candidate_id':v2.id})
 s.add(row);s.commit();return row

def test_switch_atomic_recomputes_reviews_and_preserves_human_conclusions(db):
 with Session() as s:
  claims,pending,v1,v2=_setup(s)
  # 基线生成 6 条未处理复核
  for p in v1.result['pending']:
   s.add(Review(training_id=v1.id,claim_id=p['id'],score=p['score']))
  s.commit()
  # analyst 已对其中一笔填写人工判断（未签核）；另一笔已签核
  human_draft=s.query(Review).filter_by(training_id=v1.id,claim_id=pending[0].id).first()
  human_draft.decision='flagged';human_draft.note='已核对原票据，需要进一步核实行程'
  approved=s.query(Review).filter_by(training_id=v1.id,claim_id=pending[1].id).first()
  approved.decision='valid';approved.note='票据与行程一致，核验正常';approved.status='approved'
  s.commit()
  cmp=_passed_comparison(s,v1,v2)
  outcome=perform_switch(s,cmp.id,None);s.commit()
  assert outcome['created']==2  # 候选过阈 4 笔，扣除 2 笔已有人工结论
  assert s.get(Training,v2.id).is_active is True
  assert s.get(Training,v1.id).is_active is False
  # 旧版未处理 4 笔被取代；2 笔人工结论保留原依据
  old=s.query(Review).filter_by(training_id=v1.id).all()
  superseded=[r for r in old if r.status=='superseded']
  preserved=[r for r in old if r.status!='superseded']
  assert len(superseded)==4 and all(r.superseded_by==cmp.id for r in superseded)
  assert {r.claim_id for r in preserved}=={pending[0].id,pending[1].id}
  assert human_draft.note.startswith('已核对原票据') and approved.status=='approved'
  # 新版复核不与人工结论票据重复
  new=s.query(Review).filter_by(training_id=v2.id).all()
  assert len(new)==2 and {r.claim_id for r in new}.isdisjoint({pending[0].id,pending[1].id})
  assert all(r.status=='draft' and r.decision=='pending' for r in new)

def test_switch_retry_is_idempotent_and_adds_no_new_reviews(db):
 with Session() as s:
  claims,pending,v1,v2=_setup(s)
  for p in v1.result['pending']:
   s.add(Review(training_id=v1.id,claim_id=p['id'],score=p['score']))
  s.commit()
  cmp=_passed_comparison(s,v1,v2)
  first=perform_switch(s,cmp.id,None);s.commit()
  total_after=s.query(Review).count()
  # 沿同一比较重试：幂等无操作，不新增待核票、不重复取代
  again=perform_switch(s,cmp.id,None);s.commit()
  assert again['created']==0 and again['newly_superseded']==0
  assert s.query(Review).count()==total_after==6+first['created']
  assert s.get(Training,v2.id).is_active is True

def test_switch_rollback_restores_original_basis(db):
 with Session() as s:
  claims,pending,v1,v2=_setup(s)
  for p in v1.result['pending']:
   s.add(Review(training_id=v1.id,claim_id=p['id'],score=p['score']))
  s.commit()
  cmp=_passed_comparison(s,v1,v2)
  # 清单与指针在同一事务内改写；提交前失败 -> 整体回滚，沿原比较恢复
  perform_switch(s,cmp.id,None)
  assert s.query(Review).filter_by(status='superseded').count()==6
  s.rollback()
  assert s.query(Review).filter_by(status='superseded').count()==0
  assert s.query(Review).filter_by(training_id=v2.id).count()==0
  assert s.get(Training,v1.id).is_active is True
  assert s.get(Training,v2.id).is_active is False
  # 恢复后重试可成功（该场景无人工结论，候选 4 笔过阈全部新建）
  outcome=perform_switch(s,cmp.id,None);s.commit()
  assert outcome['created']==4 and s.get(Training,v2.id).is_active is True

def test_switch_requires_passed_comparison(db):
 with Session() as s:
  claims,pending,v1,v2=_setup(s)
  row=Comparison(baseline_id=v1.id,candidate_id=v2.id,state='rejected',reject_reason='ci',result={})
  s.add(row);s.commit()
  import pytest
  from fastapi import HTTPException
  with pytest.raises(HTTPException) as e:perform_switch(s,row.id,None)
  assert e.value.status_code==409

def test_stale_comparison_after_another_switch_is_rejected(db):
 with Session() as s:
  claims,pending,v1,v2=_setup(s)
  cmp=_passed_comparison(s,v1,v2)
  # 先通过另一路径激活第三个版本，使本比较基线失效
  v3=make_version(s,'旁路版本',0.5,claims+pending,[],
                  {c.id:0.5 for c in claims},{c.id:0.5 for c in pending})
  perform_switch(s,cmp.id,None);s.commit()  # v2 生效
  # 构造一个仍以 v1 为基线的历史比较，此时 v1 已非生效版本且候选不是当前版本
  stale=Comparison(baseline_id=v1.id,candidate_id=v3.id,state='passed',result={})
  s.add(stale);s.commit()
  import pytest
  from fastapi import HTTPException
  with pytest.raises(HTTPException) as e:perform_switch(s,stale.id,None)
  assert e.value.status_code==409 and '生效版本' in e.value.detail

def test_ensure_single_active_backfills_and_dedupes(db):
 with Session() as s:
  claims=[add_claim(s,f'B{i}',amount=100+i,label=i%2) for i in range(44)]
  # 既有数据：全部未激活 -> 回填最近成功版本
  v1=make_version(s,'旧版',0.5,claims,[],{c.id:0.5 for c in claims})
  v2=make_version(s,'新版',0.5,claims,[],{c.id:0.6 for c in claims})
  v1.is_active=False;v2.is_active=False
  chosen=ensure_single_active(s)
  assert chosen==v2.id and s.get(Training,v2.id).is_active is True
  # 多个激活 -> 只保留最新
  v1.is_active=True
  ensure_single_active(s)
  assert s.get(Training,v1.id).is_active is False
  assert s.get(Training,v2.id).is_active is True
