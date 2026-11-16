ROLES=('admin','analyst','viewer')
STATES=('queued','running','done','failed')
DECISIONS=('pending','valid','flagged')
LABELS=(0,1)
# 版本比较状态机：queued 受理 -> running 计算 -> passed 可切换 / rejected 不通过（样本或泄漏）/ failed 技术失败
COMPARISON_STATES=('queued','running','passed','rejected','failed')
# 复核清单状态：draft 待处理 / approved 已签核 / superseded 被版本切换取代
REVIEW_STATUSES=('draft','approved','superseded')
# 配对评估门槛
MIN_PAIRED=20
MIN_PER_CLASS=5
BOOTSTRAP_TIMES=2000
BOOTSTRAP_SEED=3106
