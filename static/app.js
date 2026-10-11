'use strict';
let csrf = null;
let currentDay = '';
let marketItems = [];
const $ = (id) => document.getElementById(id);
const statusLabels = {UNCONFIGURED:'未配置', UNKNOWN:'待获取', ERROR:'获取失败', STALE:'已过期', EOD:'日度参考', RELEASED:'已发布'};
function el(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if(className) n.className = className; return n; }
function message(text, error=false) { $('notice').textContent = text; $('notice').classList.toggle('error',error); }
async function api(path, options={}) {
  const response = await fetch(path, {...options, headers:{'Content-Type':'application/json','X-CSRF-Token':csrf || '',...options.headers}});
  const result = await response.json();
  if (!response.ok) { if(response.status===401) showLogin(); throw new Error(result.error || '请求失败，请稍后重试'); }
  return result;
}
function showLogin(){ $('login-panel').hidden=false; $('workspace').hidden=true; }
function value(item){ return item.value === null ? '—' : new Intl.NumberFormat('zh-CN',{maximumFractionDigits:3}).format(item.value); }
function source(item) {const link=el('a',item.provider || '待接入'); if(item.source_url && /^https:\/\/(fred\.stlouisfed\.org|twelvedata\.com)\//.test(item.source_url)){link.href=item.source_url;link.target='_blank';link.rel='noopener noreferrer';} return link;}
function badge(item){return el('span',statusLabels[item.status] || item.status, 'status '+(['EOD','RELEASED'].includes(item.status)?'good':''));}
function renderMarkets(items){
  marketItems=items; $('market-grid').replaceChildren();
  for(const item of items){ const card=el('article',undefined,'market-card'); const label=el('div',undefined,'card-label');label.append(el('span',item.label),badge(item)); card.append(label,el('div',value(item),'market-value'),el('small',item.unit),el('small',item.observation_date ? '观测日 '+item.observation_date : '等待有效数据'),el('small',item.provider || '等待行情源'));
    if(item.error) card.title=item.error; if(item.fetched_at) card.append(el('small','抓取 '+new Date(item.fetched_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}))); $('market-grid').append(card); }
  if(!$('asset').options.length) for(const item of items){const opt=el('option',item.label);opt.value=item.id;$('asset').append(opt);}
}
function renderMacro(items){$('macro-table').replaceChildren();for(const item of items){const row=el('tr');const src=el('td');src.append(source(item));const status=el('td');status.append(badge(item));row.append(el('td',item.label),el('td',value(item)+(item.value===null?'':' '+item.unit)),el('td',item.observation_date || '—'),src,status);if(item.error)row.title=item.error;$('macro-table').append(row);}}
function renderBriefing(b){currentDay=b.date;$('date').textContent=b.date+' · 北京时间';$('coverage').textContent=b.coverage.available+' / '+b.coverage.total;$('coverage-note').textContent=b.coverage.available?'仅使用有来源且未过期的数据':'数据源尚未就绪，先完成配置';$('briefing-items').replaceChildren();
  if(!b.observations.length){$('briefing-items').append(el('p','尚无可核验数据。配置 FRED 密钥后可获取美国宏观和部分市场的日度参考数据。','muted'));}
  for(const item of b.observations.slice(0,3)){const card=el('article',undefined,'brief-item');card.append(el('h3',item.label),el('p',value(item)+' '+item.unit+' · '+item.observation_date),el('p',item.change===null?'暂无可比前值':'较上一有效观测变化 '+(item.change>0?'+':'')+item.change+' '+item.unit));$('briefing-items').append(card);}
  if(!$('questions').children.length)b.questions.forEach((q,i)=>{const wrap=el('div',undefined,'question');const label=el('label');label.htmlFor='answer-'+i;label.append(el('span','0'+(i+1)),document.createTextNode(q));const area=el('textarea');area.id='answer-'+i;area.required=true;area.minLength=5;area.maxLength=4000;area.placeholder='写下证据、传导逻辑和反证条件…';wrap.append(label,area);$('questions').append(wrap);});
}
function renderJournal(items){$('journal-list').replaceChildren();const today=items.find(x=>x.day===currentDay);$('save').disabled=Boolean(today);$('save-status').textContent=today?'今日已保存，原始判断已锁定':'';
  if(today){today.payload.answers.forEach((a,i)=>{$('answer-'+i).value=a;$('answer-'+i).disabled=true;});$('asset').value=today.payload.asset;$('direction').value=today.payload.direction;$('confidence').value=today.payload.confidence;}
  if(!items.length){$('journal-list').append(el('div','还没有判断记录。从今天的三问开始。','empty'));return;}
  for(const item of items){const card=el('article',undefined,'panel entry');const name=marketItems.find(m=>m.id===item.payload.asset)?.label || item.payload.asset;card.append(el('h3',item.day+' · '+name+' · '+({up:'偏多',down:'偏空',neutral:'中性'}[item.payload.direction])),el('p','信心 '+item.payload.confidence+' / 100 · 已保存当时数据快照'));
    const detail=el('details');detail.append(el('summary','查看三问与当时数据'));item.payload.answers.forEach((a,i)=>detail.append(el('p',(i+1)+'. '+a)));const snap=el('p','数据快照时间：'+item.snapshot.captured_at);detail.append(snap);for(const m of item.snapshot.market || [])detail.append(el('p',m.label+'：'+value(m)+' '+(m.unit || '')+' · '+(m.observation_date || '无数据')+' · '+(statusLabels[m.status]||m.status)));card.append(detail);
    for(const days of [1,7,30]){const existing=item.reviews.find(r=>r.horizon===days);const due=item.review_due[String(days)];card.append(el('p','T+'+days+' 复盘 · '+due+(existing?' · 已完成':due<=currentDay?' · 待复盘':' · 等待到期')));if(existing)card.append(el('p',existing.notes));else if(due<=currentDay){const form=el('form',undefined,'review-form');const text=el('textarea');text.required=true;text.minLength=5;text.maxLength=4000;text.placeholder='实际发生了什么？原来的传导逻辑是否成立？';text.setAttribute('aria-label','T+'+days+' 复盘内容');const button=el('button','保存 T+'+days+' 复盘','ghost');button.type='submit';form.append(text,button);form.addEventListener('submit',async e=>{e.preventDefault();button.disabled=true;try{await api('/api/judgments/'+item.id+'/reviews',{method:'POST',body:JSON.stringify({horizon:days,notes:text.value})});await loadJournal();message('复盘已保存');}catch(err){message(err.message,true);button.disabled=false;}});card.append(form);}}
    $('journal-list').append(card);
  }
}
async function loadJournal(){renderJournal((await api('/api/judgments')).items);}
async function load(){const [m,e,b,s]=await Promise.all([api('/api/market'),api('/api/macro'),api('/api/briefing'),api('/api/status')]);renderMarkets(m.items);renderMacro(e.items);renderBriefing(b);await loadJournal();$('provider-status').textContent='FRED '+(s.fred_configured?'已配置':'未配置')+' · 行情密钥 '+(s.market_key_configured?'已配置':'未配置');message(b.coverage.available?'数据已加载。请留意观测日期和数据状态。':'服务已就绪，数据源尚未配置。现在可以记录判断；不会生成虚构行情。');}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();try{const data=await api('/api/session',{method:'POST',body:JSON.stringify({password:$('password').value})});csrf=data.csrf;$('password').value='';$('login-panel').hidden=true;$('workspace').hidden=false;await load();}catch(err){message(err.message,true);}});
$('logout').addEventListener('click',async()=>{try{await api('/api/session',{method:'DELETE'});location.reload();}catch(err){message(err.message,true);}});
$('training-form').addEventListener('submit',async e=>{e.preventDefault();$('save').disabled=true;try{await api('/api/judgments',{method:'POST',body:JSON.stringify({answers:[0,1,2].map(i=>$('answer-'+i).value),asset:$('asset').value,direction:$('direction').value,confidence:Number($('confidence').value)})});await loadJournal();message('今日判断和数据快照已保存。到期后可在日志中填写复盘。');}catch(err){message(err.message,true);$('save').disabled=false;}});
for(const kind of ['macro','market'])$('refresh-'+kind).addEventListener('click',async()=>{try{await api('/api/refresh/'+kind,{method:'POST',body:'{}'});message('刷新已开始，供应商响应后会更新。');setTimeout(()=>load().catch(err=>message(err.message,true)),5000);}catch(err){message(err.message,true);}});
async function init(){try{const s=await api('/api/session');csrf=s.csrf;if(s.authenticated){$('login-panel').hidden=true;$('workspace').hidden=false;await load();}else{showLogin();message('请输入你的训练台访问密码');}}catch(err){message(err.message,true);}}
init();
setInterval(()=>{if(document.visibilityState==='visible'&&!$('workspace').hidden){Promise.all([api('/api/market'),api('/api/macro'),api('/api/briefing')]).then(([m,e,b])=>{renderMarkets(m.items);renderMacro(e.items);renderBriefing(b);}).catch(err=>message(err.message,true));}},60000);
