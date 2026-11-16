import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy.exc import IntegrityError
from src.config.database import db
from src.models.entities import User,Claim,Training,Review,Comparison,Audit
from src.schemas.business import Login,ClaimInput,TrainingInput,ReviewInput,ComparisonInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.comparison import build_pairing
from src.services.switch import perform_switch
from src.controllers.business import ensure_training,ensure_comparison,lock_review
router=APIRouter()
def redis_client():return redis.Redis.from_url(os.environ['REDIS_URL'])
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/claims')
def claims(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Claim).order_by(Claim.id).all()]
@router.post('/claims')
def create_claim(data:ClaimInput,u=Depends(editor),s=Depends(db)):
 r=Claim(**data.model_dump());s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'报销编号重复')
 audit(s,u,'录入报销 '+r.code);return dump(r)
@router.put('/claims/{i}')
def edit_claim(i:int,data:ClaimInput,u=Depends(editor),s=Depends(db)):
 r=s.get(Claim,i)
 if not r:raise HTTPException(404,'报销样本不存在')
 for k,v in data.model_dump().items():setattr(r,k,v)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'报销编号重复')
 audit(s,u,'修订报销 '+str(i));return dump(r)
@router.get('/trainings')
def trainings(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Training).order_by(Training.id.desc()).all()]
@router.post('/trainings')
def train(data:TrainingInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Claim).order_by(Claim.id).all()]
 has_active=s.query(Training).filter_by(is_active=True).first() is not None
 r=Training(**data.model_dump(),result={'snapshot':rows},is_active=not has_active)
 s.add(r);s.commit()
 try:redis_client().rpush('claim:trainings',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请稍后重新提交';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交训练 '+str(r.id));return dump(r)
@router.get('/trainings/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_training(s,i))
@router.get('/trainings/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_training(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功训练可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="claim-triage-{i}.json"'})
@router.post('/trainings/{i}/reviews')
def generate(i:int,u=Depends(editor),s=Depends(db)):
 r=ensure_training(s,i)
 if r.state!='done':raise HTTPException(409,'训练完成后才能生成复核任务')
 created=0
 for p in r.result['pending']:
  if p['score']>=r.threshold and not s.query(Review).filter_by(training_id=i,claim_id=p['id']).first():s.add(Review(training_id=i,claim_id=p['id'],score=p['score']));created+=1
 s.commit();audit(s,u,'生成风险复核 '+str(i));return {'created':created}
# ---- 版本比较与切换 ----
@router.get('/comparisons')
def comparisons(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Comparison).order_by(Comparison.id.desc()).all()]
@router.post('/comparisons')
def create_comparison(data:ComparisonInput,u=Depends(editor),s=Depends(db)):
 if data.baseline_id==data.candidate_id:raise HTTPException(422,'基线与候选必须是不同训练版本')
 baseline=ensure_training(s,data.baseline_id);candidate=ensure_training(s,data.candidate_id)
 for r in (baseline,candidate):
  if r.state!='done':raise HTTPException(409,f'版本 #{r.id} 未训练完成，不能参与比较')
 # 受理即固定配对样本与依据；worker 只消费冻结结果
 details,reason=build_pairing(baseline,candidate)
 frozen={'pairing':details,'reject_reason':reason,
         'baseline_id':baseline.id,'candidate_id':candidate.id}
 row=Comparison(baseline_id=baseline.id,candidate_id=candidate.id,
                state='rejected' if reason else 'queued',
                reject_reason=reason,result=frozen)
 s.add(row);s.commit()
 if not reason:
  try:redis_client().rpush('claim:comparisons',row.id)
  except redis.RedisError:row.state='failed';row.error='队列暂不可用，请稍后重新提交';s.commit();raise HTTPException(503,row.error)
 audit(s,u,('受理版本比较 ' if not reason else '拒绝版本比较 ')+str(row.id))
 return dump(row)
@router.get('/comparisons/{i}')
def comparison_detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_comparison(s,i))
@router.get('/comparisons/{i}/export')
def comparison_export(i:int,u=Depends(current),s=Depends(db)):
 row=ensure_comparison(s,i)
 if row.state not in ('passed','rejected'):raise HTTPException(409,'比较尚无结论，暂不能下载逐票依据')
 return Response(json.dumps(dump(row),ensure_ascii=False,indent=2),media_type='application/json',
                 headers={'Content-Disposition':f'attachment; filename="version-comparison-{i}.json"'})
@router.post('/comparisons/{i}/switch')
def switch(i:int,u=Depends(admin),s=Depends(db)):
 # 评分与复核清单原子切换；服务内加行锁，与签核/人工复核互斥
 try:
  outcome=perform_switch(s,i,u)
  s.commit()
 except HTTPException:s.rollback();raise
 except Exception:_exc:s.rollback();raise HTTPException(500,'切换失败，已沿原比较回滚，可重试且不会新增待核票')
 audit(s,u,f"切换生效版本至 #{outcome['candidate_id']}（比较 {i}）")
 return {'ok':True,**outcome}
@router.get('/reviews')
def reviews(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Review).order_by(Review.id.desc()).all()]
@router.put('/reviews/{i}')
def review(i:int,data:ReviewInput,u=Depends(editor),s=Depends(db)):
 r=lock_review(s,i)
 if r.status=='approved':raise HTTPException(409,'已签核复核不可修改')
 if r.status=='superseded':raise HTTPException(409,'该任务已被版本切换取代，不能修改；人工结论保留在原版本记录中')
 r.decision=data.decision;r.note=data.note;s.commit();audit(s,u,'人工复核 '+str(i));return dump(r)
@router.post('/reviews/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=lock_review(s,i)
 if r.status=='superseded':raise HTTPException(409,'该任务已被版本切换取代，不能签核')
 if r.decision=='pending' or r.status!='draft':raise HTTPException(409,'先填写人工判断，且只签核一次')
 r.status='approved';s.commit();audit(s,u,'签核复核 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
