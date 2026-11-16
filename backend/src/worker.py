import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Training,Comparison
from src.services.analysis import train
from src.services.comparison import build_pairing,evaluate_pairing
r=redis.Redis.from_url(os.environ['REDIS_URL'])
def recover(s):
 for pending in s.query(Training).filter(Training.state.in_(['queued','running'])).all():
  pending.state='queued';r.rpush('claim:trainings',pending.id)
 # 比较任务同样在启动时回到队列，比较样本已在受理时冻结
 for pending in s.query(Comparison).filter(Comparison.state.in_(['queued','running'])).all():
  pending.state='queued';r.rpush('claim:comparisons',pending.id)
 s.commit()
def run_training(s,row):
 snapshot=row.result['snapshot'];row.state='running';s.commit()
 try:
  row.result={**train(snapshot,row.threshold),'snapshot':snapshot};row.state='done'
  Path('/artifacts/'+str(row.id)+'.json').write_text(json.dumps(row.result,ensure_ascii=False))
 except Exception as e:row.state='failed';row.error=str(e)
def run_comparison(s,row):
 baseline=s.get(Training,row.baseline_id);candidate=s.get(Training,row.candidate_id)
 if not baseline or not candidate or baseline.state!='done' or candidate.state!='done':
  row.state='failed';row.error='关联训练版本缺失或未完成';return
 row.state='running';s.commit()
 try:
  # 受理时冻结的配对样本（result.pairing），worker 不重新取数
  frozen=row.result.get('pairing') if row.result else None
  if frozen is None:
   row.state='failed';row.error='比较样本未在受理时冻结';return
  if row.result.get('reject_reason'):
   row.state='rejected';row.reject_reason=row.result['reject_reason'];return
  summary=evaluate_pairing(frozen)
  row.result={'pairing':frozen,'summary':summary,
              'baseline_id':row.baseline_id,'candidate_id':row.candidate_id}
  row.state='passed' if summary['switch_eligible'] else 'rejected'
  if not summary['switch_eligible']:
   row.reject_reason='漏报金额差 95%% 置信区间 [%.2f, %.2f] 未严格大于 0，候选版本未显著降低漏报，拒绝切换'%(summary['ci_lower'],summary['ci_upper'])
 except Exception as e:
  logging.exception('comparison failed');row.state='failed';row.error=str(e)
def main():
 with Session() as recovery:recover(recovery)
 while True:
  Path('/tmp/heartbeat').touch()
  try:
   item=r.blpop(['claim:trainings','claim:comparisons'],timeout=3)
   if not item:continue
   queue,raw=item[0].decode() if isinstance(item[0],bytes) else item[0],item[1]
   with Session() as s:
    if queue=='claim:trainings':
     row=s.get(Training,int(raw))
     if not row or row.state!='queued':continue
     run_training(s,row)
    else:
     row=s.get(Comparison,int(raw))
     if not row or row.state!='queued':continue
     run_comparison(s,row)
    s.commit()
  except Exception:logging.exception('worker');time.sleep(2)
if __name__=='__main__':main()
