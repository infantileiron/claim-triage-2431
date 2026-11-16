import math,random
from src.config.database import Session
from src.models.entities import Training
from src.services.analysis import train
from src.services.comparison import build_pairing,evaluate_pairing

def _shared_rows(n_labelled=100,n_pending=8):
 rng=random.Random(3106);base=[]
 for i in range(n_labelled+n_pending):
  distance=round(rng.uniform(5,800),1);holiday=rng.random()<.25
  base.append([round(distance*rng.uniform(1.2,5.5)+rng.uniform(50,900),2),distance,holiday])
 rows_a=[];rows_b=[]
 for i,(amount,distance,holiday) in enumerate(base):
  risk=-4.7+amount/700+1.1*holiday+amount/(distance+1)/8
  label=int(rng.random()<1/(1+math.exp(-risk))) if i<n_labelled else None
  rows_a.append({'id':i+1,'code':f'R-{i:03d}','amount':amount,'distance':distance,
                 'holiday':holiday,'label':label})
  # 候选版本：同批票据、同标签，金额整体上移制造分数差异
  rows_b.append({'id':i+1,'code':f'R-{i:03d}','amount':amount+150.0,'distance':distance,
                 'holiday':holiday,'label':label})
 return rows_a,rows_b

def test_pairing_consumes_real_train_outputs(db):
 with Session() as s:
  rows_a,rows_b=_shared_rows()
  # README 固定 seed=3106：两版分层划分一致，留出集即双方均未训练的配对池
  res_a=train(rows_a,0.5,seed=3106);res_a['snapshot']=rows_a
  res_b=train(rows_b,0.5,seed=3106);res_b['snapshot']=rows_b
  v1=Training(name='真实基线',threshold=0.5,state='done',result=res_a,is_active=True)
  v2=Training(name='真实候选',threshold=0.5,state='done',result=res_b)
  s.add_all([v1,v2]);s.commit()
  details,reason=build_pairing(v1,v2)
  # 同 seed 分层划分，两版留出票据一致，且均未进任一训练集
  assert reason=='',reason
  assert len(details)>=20
  counts={v:sum(d['label']==v for d in details) for v in (0,1)}
  assert counts[0]>=5 and counts[1]>=5
  out=evaluate_pairing(details)
  assert out['sample_n']==len(details)
  assert isinstance(out['switch_eligible'],bool)
  # 冻结明细金额与差值自洽
  for d in details:
   assert abs(d['missed_amount_diff']-(d['baseline_missed_amount']-d['candidate_missed_amount']))<1e-9
  # 配对集合不包含任一版本训练编号
  train_union=set(res_a['train_codes'])|set(res_b['train_codes'])
  assert all(d['code'] not in train_union for d in details)

def test_cross_version_holdout_train_contamination_detected(db):
 # 受控跨版污染：同 seed 的干净配对池上，把一笔双方留出票据塞进候选训练集
 rows_a,rows_b=_shared_rows()
 with Session() as s:
  res_a=train(rows_a,0.5,seed=3106);res_a['snapshot']=rows_a
  res_b=train(rows_b,0.5,seed=3106);res_b['snapshot']=rows_b
  leaked=res_b['holdout'][0]['code']
  res_b['train_codes']=sorted(set(res_b['train_codes'])|{leaked})
  v1=Training(name='基线',threshold=0.5,state='done',result=res_a)
  v2=Training(name='候选',threshold=0.5,state='done',result=res_b)
  s.add_all([v1,v2]);s.commit()
  details,reason=build_pairing(v1,v2)
  # 泄漏判定优先于样本量判定
  assert reason and '训练泄漏' in reason and leaked in reason
  assert all(d['code']!=leaked for d in details)
