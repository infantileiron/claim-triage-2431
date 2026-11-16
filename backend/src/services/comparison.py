"""版本配对评估：两版在双方均未训练过的同批已标注票据上比较漏报金额。

口径要点：
- 配对样本在受理时冻结到 Comparison.result，worker 只对冻结样本计算，不重新取数；
- 配对池取两版留出集交集；进入双方留出评估却出现在任一版训练集中的票据判为跨版
  训练泄漏并整体拒绝，仅用于训练（不在留出交集）的票据静默剔除；
- 样本不足（<20）或缺类别（每类<5）拒绝；漏报金额差用固定 seed 的配对 bootstrap
  求 95% 置信区间；
- 只有置信区间下界严格大于 0（候选漏报金额显著更少）才允许切换。
"""
import numpy as np
from src.constants.enums import MIN_PAIRED,MIN_PER_CLASS,BOOTSTRAP_TIMES,BOOTSTRAP_SEED

def _version_facts(training):
    """从冻结的训练结果提取：留出分数字典、训练编号集合、阈值。"""
    result=training.result or {}
    holdout={int(r['id']):float(r['score']) for r in result.get('holdout',[])}
    train_codes=set(result.get('train_codes',[]))
    snapshot_codes={r['code']:r for r in result.get('snapshot',[])}
    return holdout,train_codes,snapshot_codes,float(training.threshold)

def build_pairing(baseline,candidate):
    """返回 (details, reject_reason)。details 为受理时冻结的逐票配对明细。

    配对池：双方留出集中都有评分、且未被任一版本拿去训练的同批已标注票据。
    仅用于训练（不在任一留出集）的票据静默剔除；跨版交叉污染（一方评估、
    另一方训练）才判训练泄漏并拒绝；最终样本不足或缺类别拒绝。
    """
    b_holdout,b_train,b_snap,_=_version_facts(baseline)
    c_holdout,c_train,c_snap,c_threshold=_version_facts(candidate)
    details=[];leakage=[];excluded=0
    for row in baseline.result.get('snapshot',[]):
        if row.get('label') is None:
            continue
        cid=int(row['id']);code=row['code']
        # 必须在双方留出集都有评分，否则不具备配对条件（训练专用或划分不一致）
        if cid not in b_holdout or cid not in c_holdout:
            excluded+=1;continue
        other=c_snap.get(code)
        if other is None or other.get('label')!=row.get('label'):
            excluded+=1;continue
        # 交叉污染：进入双方留出评估的票据竟出现在任一版训练集中
        if code in b_train or code in c_train:
            leakage.append(code);continue
        b_score=b_holdout[cid];c_score=c_holdout[cid]
        label=int(row['label']);amount=float(row['amount'])
        b_pred=1 if b_score>=float(baseline.threshold) else 0
        c_pred=1 if c_score>=c_threshold else 0
        b_miss=1 if label==1 and b_pred==0 else 0
        c_miss=1 if label==1 and c_pred==0 else 0
        b_fp=1 if label==0 and b_pred==1 else 0
        c_fp=1 if label==0 and c_pred==1 else 0
        details.append({'claim_id':cid,'code':code,'label':label,'amount':amount,
                        'baseline_score':b_score,'candidate_score':c_score,
                        'baseline_pred':b_pred,'candidate_pred':c_pred,
                        'baseline_missed':b_miss,'candidate_missed':c_miss,
                        'baseline_false_positive':b_fp,'candidate_false_positive':c_fp,
                        'baseline_missed_amount':b_miss*amount,
                        'candidate_missed_amount':c_miss*amount,
                        'missed_amount_diff':b_miss*amount-c_miss*amount})
    reason=''
    if leakage:
        reason='存在训练泄漏，拒绝比较：以下票据进入双方留出评估却出现在训练集中 '+_short(leakage)
    elif len(details)<MIN_PAIRED:
        reason=f'配对样本不足：仅 {len(details)} 笔双方均未训练且都有留出评分的已标注票据，要求至少 {MIN_PAIRED} 笔（另有 {excluded} 笔因划分不一致被剔除）'
    else:
        per_class={v:sum(d['label']==v for d in details) for v in (0,1)}
        absent=[v for v in (0,1) if per_class[v]<MIN_PER_CLASS]
        if absent:
            reason=f'类别不完整：类别 {absent} 配对样本各需至少 {MIN_PER_CLASS} 笔，当前计数 {per_class}'
    return details,reason

def _short(codes):
    shown=','.join(sorted(codes)[:8])
    return shown+(' …' if len(codes)>8 else '')

def _confusion(details,pred_key):
    tp=sum(d['label']==1 and d[pred_key]==1 for d in details)
    fp=sum(d['label']==0 and d[pred_key]==1 for d in details)
    fn=sum(d['label']==1 and d[pred_key]==0 for d in details)
    tn=sum(d['label']==0 and d[pred_key]==0 for d in details)
    precision=tp/(tp+fp) if tp+fp else 0.0
    recall=tp/(tp+fn) if tp+fn else 0.0
    return {'tp':tp,'fp':fp,'fn':fn,'tn':tn,'precision':precision,'recall':recall,
            'f1':2*precision*recall/(precision+recall) if precision+recall else 0.0,
            'miss_rate':fn/(tp+fn) if tp+fn else 0.0,
            'false_positive_rate':fp/(fp+tn) if fp+tn else 0.0}

def evaluate_pairing(details):
    """对冻结的配对明细聚合：漏报金额、逐票差值与配对 bootstrap 95% 置信区间。"""
    rng=np.random.default_rng(BOOTSTRAP_SEED)
    n=len(details)
    diffs=np.array([d['missed_amount_diff'] for d in details],dtype=float)
    b_missed=sum(d['baseline_missed_amount'] for d in details)
    c_missed=sum(d['candidate_missed_amount'] for d in details)
    point=float(diffs.sum())
    idx=rng.integers(0,n,size=(BOOTSTRAP_TIMES,n))
    boot=diffs[idx].sum(axis=1)
    lower,upper=np.percentile(boot,[2.5,97.5])
    # 下界严格大于 0：候选漏报金额显著更少，才允许切换
    eligible=bool(lower>0)
    counts={v:sum(d['label']==v for d in details) for v in (0,1)}
    return {'sample_n':n,'label_counts':counts,'bootstrap_times':BOOTSTRAP_TIMES,'seed':BOOTSTRAP_SEED,
            'baseline_missed_amount':float(b_missed),'candidate_missed_amount':float(c_missed),
            'missed_amount_diff':point,
            'ci_lower':float(lower),'ci_upper':float(upper),'switch_eligible':eligible,
            'baseline_missed_count':sum(d['baseline_missed'] for d in details),
            'candidate_missed_count':sum(d['candidate_missed'] for d in details),
            'baseline_false_positive_count':sum(d['baseline_false_positive'] for d in details),
            'candidate_false_positive_count':sum(d['candidate_false_positive'] for d in details),
            'baseline_confusion':_confusion(details,'baseline_pred'),
            'candidate_confusion':_confusion(details,'candidate_pred'),
            'details':details}
