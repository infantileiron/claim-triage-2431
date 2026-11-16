"""版本切换：评分与复核清单在同一事务内原子切换。

规则：
- 仅可基于 passed 比较、且候选未被后续切换取代时执行；
- 旧版本未处理（draft/pending）复核标记 superseded，已有人工结论（draft 已填写、approved）保留原依据；
- 按候选版本待标注评分重建高分复核，已有任何人工结论的票据不重复建待核票，重试不新增；
- 激活指针翻转与清单变更同事务提交，任一步失败整体回滚，沿原比较重试结果一致。
"""
from fastapi import HTTPException
from sqlalchemy import select
from src.models.entities import Training,Review,Comparison

def _lock_all(s,model):
    # with_for_update 在 MySQL/InnoDB 上行锁；SQLite 退化为无操作但单连接写仍串行
    return s.scalars(select(model).with_for_update()).all()

def ensure_single_active(s):
    """启动回填：无生效版本时激活最近一个成功训练；多于一个时只保留最新。"""
    done=s.query(Training).filter_by(state='done').order_by(Training.id.desc()).all()
    active=[t for t in done if t.is_active]
    if not active:
        if done:done[0].is_active=True
    else:
        for t in active[1:]:t.is_active=False
    return done[0].id if done else None

def perform_switch(s,comparison_id,actor):
    _lock_all(s,Training)
    comp=s.get(Comparison,comparison_id)
    if not comp:
        raise HTTPException(404,'版本比较不存在')
    if comp.state!='passed':
        raise HTTPException(409,'只有通过显著性检验（passed）的比较才能切换')
    candidate=s.get(Training,comp.candidate_id)
    baseline=s.get(Training,comp.baseline_id)
    if not candidate or candidate.state!='done':
        raise HTTPException(409,'候选训练版本不可用')
    # 该比较只允许从它记录的生效版本切换，防止沿过期比较重复切换
    if baseline and not baseline.is_active:
        active=s.query(Training).filter_by(is_active=True).first()
        if active and active.id!=candidate.id:
            raise HTTPException(409,'比较基线已不是当前生效版本，请重新受理比较')
    _lock_all(s,Review)
    existing_reviews=s.query(Review).all()
    # 已有任何人工结论的票据：保留原依据，切换不新建待核票
    human_claim_ids={r.claim_id for r in existing_reviews
                     if r.status=='approved' or r.decision!='pending'}
    candidate_review_pairs={(r.training_id,r.claim_id) for r in existing_reviews}
    newly_superseded=0
    for r in existing_reviews:
        # 幂等：重试时已被本比较取代的记录不再重复计数或改写
        if r.training_id==comp.baseline_id and r.status=='draft' and r.decision=='pending' \
           and r.superseded_by is None:
            r.status='superseded';r.superseded_by=comparison_id;newly_superseded+=1
    created=0
    for p in (candidate.result or {}).get('pending',[]):
        if p['score']<candidate.threshold:
            continue
        cid=p['id']
        if cid in human_claim_ids:
            continue
        if (candidate.id,cid) in candidate_review_pairs:
            continue
        s.add(Review(training_id=candidate.id,claim_id=cid,score=float(p['score']),
                     decision='pending',status='draft'))
        created+=1
    s.flush()
    total_superseded=s.query(Review).filter_by(superseded_by=comparison_id).count()
    for t in s.query(Training).all():
        t.is_active=(t.id==candidate.id)
    return {'candidate_id':candidate.id,'created':created,'newly_superseded':newly_superseded,
            'superseded_total':total_superseded,
            'preserved_human_claims':sorted(human_claim_ids)}
