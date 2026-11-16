from fastapi import HTTPException
from sqlalchemy import select
from src.models.entities import Training,Review
def ensure_training(s,i):
 row=s.get(Training,i)
 if not row:raise HTTPException(404,'训练版本不存在')
 return row
def ensure_comparison(s,i):
 from src.models.entities import Comparison
 row=s.get(Comparison,i)
 if not row:raise HTTPException(404,'版本比较不存在')
 return row
def lock_review(s,i):
 # 行锁：与版本切换互斥，保护切换窗口内的人工/签核结论
 row=s.scalars(select(Review).where(Review.id==i).with_for_update()).first()
 if not row:raise HTTPException(404,'复核任务不存在')
 return row
