import {state} from '../stores/session.js';import {esc} from '../utils/html.js';import {badge,table} from './shared.js';import {select} from '../components/common/fields.js';import {COMPARISON_STATE_TEXT,REVIEW_STATUS_TEXT} from '../constants/enums.js';
const money=v=>'¥'+Number(v).toFixed(2);
const pc=v=>(100*v).toFixed(1)+'%';
const stateText=s=>COMPARISON_STATE_TEXT[s]||s;
function summary(c){
 let r=c.result?.summary;if(!r)return '';
 let ok=r.switch_eligible;
 return `<div class="metrics">
 <div><strong>${r.sample_n}</strong>配对票据（异常 ${r.label_counts['1']} / 正常 ${r.label_counts['0']}）</div>
 <div><strong>${money(r.baseline_missed_amount)}</strong>基线漏报金额</div>
 <div><strong>${money(r.candidate_missed_amount)}</strong>候选漏报金额</div>
 <div><strong class="${ok?'ok':'no'}">${money(r.missed_amount_diff)}</strong>漏报金额差(基-候)</div>
 <div><strong class="${ok?'ok':'no'}">[${money(r.ci_lower)}, ${money(r.ci_upper)}]</strong>95% 置信区间</div>
 </div>
 <p class="notice">${ok?'置信区间下界严格大于 0，候选版本漏报金额显著更低，负责人可执行原子切换。':'置信区间未严格大于 0，候选未显著降低漏报，禁止切换。'} 配对 bootstrap 重采样 ${r.bootstrap_times} 次，seed ${r.seed}。</p>
 <h3>同批配对指标（各自阈值口径）</h3>
 ${table(['版本','漏报笔数','误报笔数','Recall','Precision','F1','漏报率'],[
  ['基线 #'+c.baseline_id,r.baseline_missed_count,r.baseline_false_positive_count,pc(r.baseline_confusion.recall),pc(r.baseline_confusion.precision),pc(r.baseline_confusion.f1),pc(r.baseline_confusion.miss_rate)],
  ['候选 #'+c.candidate_id,r.candidate_missed_count,r.candidate_false_positive_count,pc(r.candidate_confusion.recall),pc(r.candidate_confusion.precision),pc(r.candidate_confusion.f1),pc(r.candidate_confusion.miss_rate)]])}`;
}
function details(c){
 let rows=(c.result?.pairing||[]).map(d=>[d.code,d.label===1?'异常':'正常',money(d.amount),
  pc(d.baseline_score),pc(d.candidate_score),d.baseline_pred?'异常':'正常',d.candidate_pred?'异常':'正常',
  d.baseline_missed?money(d.amount):'—',d.candidate_missed?money(d.amount):'—',money(d.missed_amount_diff)]);
 return `<h3>逐票配对依据（受理后固定，共 ${rows.length} 笔）</h3>
 ${table(['票据','人工标签','金额','基线分','候选分','基线预测','候选预测','基线漏报金额','候选漏报金额','差值'],rows)}`;
}
export function render(){
 let c=state.selectedComparison;
 let done=state.trainings.filter(t=>t.state==='done');
 return `<div class="split"><section class="panel">
 <h2>版本配对比较与切换</h2>
 <p class="notice">仅在双方均未训练过的同批已标注票据上配对评估；受理时固定样本与依据，样本不足、缺类别或训练泄漏直接拒绝。漏报金额差 95% 置信区间下界 &gt; 0 才允许切换。</p>
 ${c?`<div class="result-head"><h3>比较 #${c.id} · 基线 #${c.baseline_id} → 候选 #${c.candidate_id} ${badge(stateText(c.state))}</h3>
 ${state.user.role==='admin'&&c.state==='passed'&&!state.trainings.find(t=>t.id===c.candidate_id)?.is_active?`<button id="switch" data-switch="${c.id}" class="primary">确认显著下降，原子切换</button>`:''}
 ${c.state==='passed'&&state.trainings.find(t=>t.id===c.candidate_id)?.is_active?badge('候选已生效'):''}
 <button id="comparison-download" data-cmp="${c.id}">下载逐票依据 JSON</button></div>
 ${c.state==='rejected'||c.state==='failed'?`<p class="notice no">${esc(c.reject_reason||c.error||'比较未通过')}</p>`:''}
 ${summary(c)}${c.result?.summary?details(c):''}`
 :'<p class="notice">选择左侧比较，或在右侧受理一次新版本比较。</p>'}
 <h3>比较记录</h3>
 ${state.comparisons.map(x=>`<button class="study" data-comparison="${x.id}">#${x.id} 基线#${x.baseline_id}→候选#${x.candidate_id} · ${esc(stateText(x.state))}</button>`).join('')||'<p>暂无比较。</p>'}
 </section><aside class="side"><h2>受理版本比较</h2>
 <form id="comparison">
 ${select('当前生效 / 基线版本','baseline_id',done.map(t=>[t.id,'#'+t.id+' '+t.name+(t.is_active?'（生效中）':'')]))}
 ${select('待评估 / 候选版本','candidate_id',done.map(t=>[t.id,'#'+t.id+' '+t.name]))}
 <p>受理即冻结配对样本：两版训练集均不含、且双方都有留出评分的已标注票据。</p>
 <button class="primary">固定样本并评估</button>
 </form></aside></div>`;
}
