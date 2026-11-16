import {state} from '../stores/session.js';import {esc} from '../utils/html.js';import {badge} from '../components/common/status.js';import {select} from '../components/common/fields.js';import {REVIEW_STATUS_TEXT} from '../constants/enums.js';
const statusText=s=>REVIEW_STATUS_TEXT[s]||s;
const trainingName=id=>{let t=state.trainings.find(x=>x.id===id);return t?`#${id} ${t.name}`:('#'+id)};
export function render(){
 let active=state.trainings.find(t=>t.is_active);
 return `<div class="split"><section class="panel"><h2>人工判断保留独立证据</h2>
 <p class="notice">当前生效评分版本：${active?esc(trainingName(active.id))+'（#'+active.id+'）':'尚未激活，首个成功训练自动生效'}。模型分数只排序，不自动确认异常。版本切换只取代未处理任务；已填写或已签核的人工结论保留原依据，不重复建待核票。</p>
 ${state.reviews.map(r=>{
  let superseded=r.status==='superseded';
  let canApprove=state.user.role==='admin'&&r.status==='draft'&&r.decision!=='pending';
  return `<article class="rec ${superseded?'muted':''}"><h3>复核 #${r.id} · 样本 #${r.claim_id} ${badge(statusText(r.status))} ${state.trainings.find(t=>t.id===r.training_id)?.is_active?'':badge('历史版本')}</h3>
  <p>${esc(trainingName(r.training_id))} · 异常概率 ${(r.score*100).toFixed(1)}% · ${esc(r.decision)}</p>
  <p>${esc(r.note||(superseded?'该待处理任务已被版本切换取代，由新版本重新评分。':'等待人工复核'))}</p>
  ${superseded&&r.superseded_by?`<p class="eyebrow">由比较 #${r.superseded_by} 的原子切换取代</p>`:''}
  ${canApprove?`<button data-approve="${r.id}">负责人签核</button>`:''}</article>`;
 }).join('')||'<p>暂无任务。在分类评估中生成阈值以上的未标注候选。</p>'}</section>
 <aside class="side"><h2>填写复核结论</h2><form id="review">
 ${select('待签核任务','record_id',state.reviews.filter(r=>r.status==='draft').map(r=>[r.id,'#'+r.id+' 样本#'+r.claim_id]))}
 ${select('人工判断','decision',[['valid','核验正常'],['flagged','需进一步核实']])}
 <label>理由与证据<textarea name="note" minlength="6" required rows="6"></textarea></label>
 <button class="primary">保存人工复核</button></form></aside></div>`}
