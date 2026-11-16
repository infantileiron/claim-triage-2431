import {esc} from '../utils/html.js';
export const badge=s=>`<span class=tag>${esc(s)}</span>`;
export function table(headers,rows){return `<div class=scroll><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')||'<tr><td>暂无记录。</td></tr>'}</tbody></table></div>`}
