from src.config.database import Session
from src.models.entities import Training
from src.services.comparison import build_pairing,evaluate_pairing
from src.constants.enums import MIN_PAIRED,MIN_PER_CLASS
from conftest import add_claim,make_version

def _labelled_set(s,prefix,n1=12,n0=12,high_amount=6000.0):
 claims=[]
 # 异常类：高金额；正常类：低金额
 for i in range(n1):
  claims.append(add_claim(s,f'{prefix}-A{i:02d}',amount=high_amount+i,distance=50,label=1))
 for i in range(n0):
  claims.append(add_claim(s,f'{prefix}-N{i:02d}',amount=200.0+i,distance=50,label=0))
 return claims

def test_pairing_rejects_insufficient_sample_and_missing_class(db):
 with Session() as s:
  claims=_labelled_set(s,'S',n1=8,n0=8)  # 16 < 20
  ids=[c.id for c in claims]
  v1=make_version(s,'v1',0.5,claims,[],{cid:0.9 for cid in ids})
  v2=make_version(s,'v2',0.5,claims,[],{cid:0.1 for cid in ids})
  details,reason=build_pairing(v1,v2)
  assert reason and '样本不足' in reason and len(details)==16
  # 缺类别：24 笔异常、0 笔正常（样本量达标但缺类别）
  only_anom=_labelled_set(s,'SC',n1=24,n0=0)
  ids2=[c.id for c in only_anom]
  w1=make_version(s,'w1',0.5,only_anom,[],{cid:0.9 for cid in ids2})
  w2=make_version(s,'w2',0.5,only_anom,[],{cid:0.1 for cid in ids2})
  details,reason=build_pairing(w1,w2)
  assert reason and '类别不完整' in reason

def test_training_leakage_rejects_comparison(db):
 with Session() as s:
  claims=_labelled_set(s,'L')
  ids=[c.id for c in claims]
  v1=make_version(s,'base',0.5,claims,[],{cid:0.9 for cid in ids})
  # 交叉污染：该票据在双方留出集都有评分，却被候选拿去训练
  leaked=claims[0]
  v2=make_version(s,'cand',0.5,claims,[leaked.code],{cid:0.9 for cid in ids})
  details,reason=build_pairing(v1,v2)
  assert reason and '训练泄漏' in reason and leaked.code in reason
  assert all(d['code']!=leaked.code for d in details)

def test_train_only_claims_are_silently_excluded_not_rejected(db):
 with Session() as s:
  claims=_labelled_set(s,'T')
  ids=[c.id for c in claims]
  # 两版都把同一批票据当训练集（不在留出集），其余正常配对
  train_codes=[c.code for c in claims[:4]]
  hold={cid:0.9 for cid in ids if cid not in [c.id for c in claims[:4]]}
  v1=make_version(s,'base',0.5,claims,train_codes,hold)
  v2=make_version(s,'cand',0.5,claims,train_codes,dict(hold))
  details,reason=build_pairing(v1,v2)
  assert reason=='' and len(details)==20
  excluded={c.code for c in claims[:4]}
  assert all(d['code'] not in excluded for d in details)

def test_significant_drop_passes_and_ci_strictly_positive(db):
 with Session() as s:
  claims=_labelled_set(s,'P',high_amount=8000.0)
  ids=[c.id for c in claims]
  # 基线：所有异常票据分数低于阈值 -> 全部漏报
  base_scores={cid:(0.1 if s.get(type(claims[0]),cid).label==1 else 0.1) for cid in ids}
  # 候选：异常票据全部高分命中，正常保持低分
  cand_scores={}
  for c in claims:
   cand_scores[c.id]=0.9 if c.label==1 else 0.1
  v1=make_version(s,'base',0.5,claims,[],base_scores,active=True)
  v2=make_version(s,'cand',0.5,claims,[],cand_scores)
  details,reason=build_pairing(v1,v2)
  assert reason=='' and len(details)==24
  out=evaluate_pairing(details)
  assert out['switch_eligible'] is True
  assert out['ci_lower']>0 and out['ci_upper']>=out['ci_lower']
  assert out['candidate_missed_count']==0
  assert out['baseline_missed_amount']>out['candidate_missed_amount']
  assert out['baseline_confusion']['recall']==0 and out['candidate_confusion']['recall']==1
  # 逐票明细完整冻结
  assert set(details[0].keys())>={'claim_id','code','label','amount','baseline_score',
                                   'candidate_score','baseline_pred','candidate_pred',
                                   'missed_amount_diff'}

def test_non_significant_equal_versions_not_eligible(db):
 with Session() as s:
  claims=_labelled_set(s,'E')
  ids=[c.id for c in claims]
  scores={cid:(0.9 if s.get(type(claims[0]),cid).label==1 else 0.1) for cid in ids}
  v1=make_version(s,'base',0.5,claims,[],scores,active=True)
  v2=make_version(s,'cand',0.5,claims,[],dict(scores))
  details,reason=build_pairing(v1,v2)
  assert reason==''
  out=evaluate_pairing(details)
  # 两版完全一致：差值恒为 0，CI 下界不大于 0，禁止切换
  assert out['missed_amount_diff']==0 and out['ci_lower']==0
  assert out['switch_eligible'] is False

def test_pairing_only_uses_claims_with_both_holdout_scores(db):
 with Session() as s:
  claims=_labelled_set(s,'H')
  ids=[c.id for c in claims]
  v1=make_version(s,'base',0.5,claims,[],{cid:0.9 for cid in ids})
  partial=dict(zip(ids,[0.9]*len(ids)))
  del partial[ids[0]]  # 候选缺少该票据留出评分：该票被剔除，其余正常配对
  v2=make_version(s,'cand',0.5,claims,[],partial)
  details,reason=build_pairing(v1,v2)
  assert reason=='' and len(details)==23
  assert all(d['claim_id']!=ids[0] for d in details)
  # 剔除过多导致不足 20 笔时拒绝
  few=dict(list(zip(ids,[0.9]*len(ids)))[:10])
  v3=make_version(s,'cand2',0.5,claims,[],few)
  details,reason=build_pairing(v1,v3)
  assert reason and '配对样本不足' in reason
