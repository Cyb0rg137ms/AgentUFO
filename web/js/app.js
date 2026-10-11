(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { online:false, status:{}, models:{}, selected:[], runs:[], activeRun:null, eventSeq:{}, events:[], agentCount:3, tree:null, activeFile:null, lastStatusAt:0, polling:false, chatMessages:[], chatSending:false, chatTokenSaving:true, reportedChatRuns:new Set() };
  const providers = ['openai','anthropic','grok','gemini','deepseek','kimi','minimax','qwen','ollama','local'];
  const modelMeta = {openai:['OpenAI','◉'],anthropic:['Anthropic','✳'],claude:['Anthropic Claude','✳'],grok:['xAI','✦'],gemini:['Google Gemini','◇'],deepseek:['DeepSeek','◈'],kimi:['Moonshot / Kimi','☾'],minimax:['MiniMax','M'],qwen:['Qwen / DashScope','Q'],ollama:['Ollama · local','⌂'],local:['OpenAI-compatible endpoint','⌘']};
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (n) => Number(n||0).toLocaleString();
  const time = (ts) => new Date((ts||Date.now()/1000)*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  const humanStatus = (s) => String(s||'unknown').replaceAll('_',' ').toUpperCase();
  const terminal = (s) => ['completed','failed','failed_review','failed_review','stopped','interrupted','budget_exceeded','stopped_budget','error'].includes(s);
  async function api(path, options={}) {
    const res = await fetch(path, {headers:{'Content-Type':'application/json',...(options.headers||{})}, ...options});
    let data; try { data = await res.json(); } catch { data = {error:`HTTP ${res.status}`}; }
    if (!res.ok) throw new Error(data.error || data.message || `Request failed (${res.status})`);
    return data;
  }
  async function post(path, data={}) { return api(path,{method:'POST',body:JSON.stringify(data)}); }
  function toast(message, kind='info') {
    const node=document.createElement('div'); node.className=`toast ${kind}`; node.textContent=message; $('toast-region').append(node); setTimeout(()=>node.remove(),4300);
  }
  function navigate(view) {
    document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${view}`));
    document.querySelectorAll('.rail-btn').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
    const labels={chat:'Chat',control:'Control room',workspace:'Workspace',models:'Model routing',settings:'System settings'};
    $('page-title').innerHTML=`${labels[view]||'Control room'} <span class="live-tag"><i></i> LIVE</span>`;
    if(view==='workspace') loadTree(); if(view==='models') loadModels(); if(view==='settings') loadSettings();
  }
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.view)));
  $('btn-open-models').addEventListener('click',()=>navigate('models'));
  $('btn-open-models-settings').addEventListener('click',()=>navigate('models'));
  $('btn-minimize-tray').addEventListener('click',()=>toggleTray()); $('btn-collapse-tray').addEventListener('click',()=>toggleTray());
  function toggleTray(){ $('activity-tray').classList.toggle('collapsed'); }
  $('btn-clear-events').addEventListener('click',()=>{state.events=[];renderTimeline();});
  $('agent-minus').addEventListener('click',()=>setAgentCount(state.agentCount-1)); $('agent-plus').addEventListener('click',()=>setAgentCount(state.agentCount+1));
  function setAgentCount(n){state.agentCount=Math.max(3,Math.min(16,n));$('agent-count-label').textContent=state.agentCount;}
  document.querySelectorAll('.prompt-chip').forEach(b=>b.addEventListener('click',()=>{$('task-prompt').value=b.dataset.prompt;$('task-prompt').focus();}));

  function selectedModelText(){
    const models=state.selected.map(k=>findModel(k)).filter(Boolean);
    $('selected-fleet').innerHTML=models.length?models.map(m=>`<span class="state-pill">${esc(m.provider)} / ${esc(m.model)}</span>`).join(''):`<span class="empty-dot"></span> Select one or more models in <button class="inline-link" data-view="models">Model routing</button>.`;
    $('selected-fleet').querySelector('[data-view]')?.addEventListener('click',()=>navigate('models'));
    $('metric-models').textContent=models.length; $('metric-model-foot').textContent=models.length?`${new Set(models.map(m=>m.provider)).size} provider(s) selected`:'Select a model fleet';
    $('model-selection-count').textContent=`${models.length} selected`;
  }
  function findModel(key){return state.availableModels?.find(m=>m.key===key);}
  function addModel(provider, model, ready=false){
    const key=`${provider}::${model}`; if(!state.availableModels.some(m=>m.key===key)) state.availableModels.push({key,provider,model,ready});
  }
  async function loadModels(){
    $('model-list').innerHTML='<div class="loading-line">Probing installed models and provider catalog…</div>';
    try{
      const [catalog,status,ollama]=await Promise.all([api('/api/models'),api('/api/status'),api('/api/ollama/status').catch(()=>({}))]);
      state.models=catalog; state.status=status; state.availableModels=[];
      const installed=Array.isArray(catalog.local_models)?catalog.local_models:[];
      installed.forEach(x=>addModel('ollama',typeof x==='string'?x:(x.name||x.model||''),true));
      const presets=catalog.presets||{};
      for(const p of providers){if(p==='ollama')continue; (presets[p]||[]).forEach(m=>addModel(p,m,Boolean(status.configured_api_keys?.[p])));}
      (presets.claude||[]).forEach(m=>addModel('anthropic',m,Boolean(status.configured_api_keys?.anthropic)));
      // Current model is always represented, even if provider catalog omits it.
      if(status.active_provider&&status.active_model&&status.active_provider!=='unconfigured') addModel(status.active_provider,status.active_model,Boolean(status.provider_available));
      const saved=readSelected(); state.selected=saved.filter(k=>state.availableModels.some(m=>m.key===k)).slice(0,3);saveSelected();
      renderModels(); selectedModelText(); renderChatModelSelector(); renderOllamaStatus(ollama);
    }catch(e){$('model-list').innerHTML=`<div class="loading-line">Could not load model catalog: ${esc(e.message)}</div>`;toast(e.message,'error');}
  }
  function readSelected(){try{return JSON.parse(localStorage.getItem('agentufo_model_fleet')||'[]')}catch{return[]}}
  function saveSelected(){try{localStorage.setItem('agentufo_model_fleet',JSON.stringify(state.selected))}catch{}}
  function renderModels(){
    if(!state.availableModels?.length){$('model-list').innerHTML='<div class="loading-line">No model catalog returned. Configure a provider or install Ollama.</div>';return;}
    const groups=[...new Set(state.availableModels.map(m=>m.provider))];
    $('model-list').innerHTML=groups.map(p=>`<div class="eyebrow" style="margin:11px 0 2px">${esc(modelMeta[p]?.[0]||p.toUpperCase())}</div>`+state.availableModels.filter(m=>m.provider===p).map(m=>`<label class="model-row ${state.selected.includes(m.key)?'selected':''}"><input type="checkbox" data-model-key="${esc(m.key)}" ${state.selected.includes(m.key)?'checked':''}><span class="model-symbol">${esc(modelMeta[p]?.[1]||'⌘')}</span><span class="model-info"><strong>${esc(m.model)}</strong><small>${esc(p)} · ${m.ready?'available / selected provider':'catalog option; availability not verified'}</small></span><span class="model-status ${m.ready?'ready':''}">${m.ready?'READY':'UNVERIFIED'}</span></label>`).join('')).join('');
    $('model-list').querySelectorAll('[data-model-key]').forEach(input=>input.addEventListener('change',()=>{
      const key=input.dataset.modelKey; if(input.checked){if(state.selected.length>=3){input.checked=false;toast('This verified pipeline has three active roles. Select at most three model assignments; additional-seat routing is not enabled in this build.','error');return;}if(!state.selected.includes(key))state.selected.push(key);}else state.selected=state.selected.filter(k=>k!==key);
      saveSelected(); renderModels(); selectedModelText();
    }));
  }
  function renderChatModelSelector(){
    const select=$('chat-active-model'); if(!select)return;
    const previous=select.value;
    select.innerHTML='<option value="">Choose a model…</option>'+(state.availableModels||[]).map(m=>`<option value="${esc(m.key)}">${esc((modelMeta[m.provider]?.[0]||m.provider)+' · '+m.model)}</option>`).join('');
    const wanted=(state.availableModels||[]).some(m=>m.key===previous)?previous:(state.selected[0]||'');select.value=wanted;
  }
  $('btn-model-refresh').addEventListener('click',loadModels);
  $('btn-apply-models').addEventListener('click',async()=>{
    saveSelected(); selectedModelText();
    const selected=state.selected.map(findModel).filter(Boolean).map(m=>({provider:m.provider,model:m.model}));
    try{await post('/api/crew/configure',{multi_llm_fleet:selected.map((m,i)=>({...m,id:`${m.provider}-${m.model}`,name:`${m.provider} / ${m.model}`,role:['Architect','Builder','Reviewer','QA / Worker'][i]||`Worker ${i+1}`,active:true})), num_seats:state.agentCount});toast(`Applied ${selected.length} model(s) to the crew configuration.`,'success');}catch(e){toast(e.message,'error');}
    navigate('control');
  });
  function renderOllamaStatus(data={}){
    const online=Boolean(data.daemon_running??data.running??data.online);
    $('ollama-status-title').textContent=online?'Ollama is reachable':'Ollama is not running';
    $('ollama-status-copy').textContent=online?`${(data.installed_models||data.models||[]).length} installed model(s) detected. Local model availability is probed, not assumed.`:(data.installed?'Installed, but the daemon is unavailable.':'Start Ollama if installed, or install it from ollama.com.');
    const models=data.installed_models||data.models||[]; if(Array.isArray(models)&&models.length){const sel=$('ollama-model-select'); const previous=sel.value;sel.innerHTML=models.map(x=>{const name=typeof x==='string'?x:(x.name||x.model);return `<option value="${esc(name)}">${esc(name)}</option>`}).join('');if(models.some(x=>(typeof x==='string'?x:x.name)===previous))sel.value=previous;}
  }
  $('btn-detect-key').addEventListener('click',async()=>{
    const input=$('api-key'),key=input.value.trim();if(!key){toast('Paste an API key first.','error');return;}
    const result=$('key-detect-result');result.className='inline-result';result.textContent='Detecting provider and checking the key…';$('btn-detect-key').disabled=true;
    try{const data=await post('/api/detect_key',{api_key:key});input.value='';if(data.status==='success'){
      result.className='inline-result success';result.textContent=`Detected ${data.provider_name||data.provider} · default model ${data.model}${data.verified?' · verified':''}.`;
      addModel(data.provider,data.model,Boolean(data.verified));const k=`${data.provider}::${data.model}`;if(!state.selected.includes(k))state.selected.unshift(k);saveSelected();selectedModelText();toast(`Connected ${data.provider_name||data.provider}.`,'success');
    }else{result.className='inline-result error';result.textContent=data.message||data.error||'Provider could not be detected. No settings were changed.';toast(result.textContent,'error');}}
    catch(e){input.value='';result.className='inline-result error';result.textContent=e.message;toast(e.message,'error');}finally{$('btn-detect-key').disabled=false;}
  });
  $('btn-start-ollama').addEventListener('click',async()=>{const b=$('btn-start-ollama');b.disabled=true;b.textContent='Starting…';try{const r=await post('/api/ollama/launch',{});toast(r.message||r.error||(r.success?'Ollama launch requested':'Could not start Ollama'),r.success?'success':'error');await loadModels();}catch(e){toast(e.message,'error')}finally{b.disabled=false;b.textContent='Start Ollama';}});
  async function streamOllamaPull(model, button) {
    const response=await fetch(`/api/ollama/pull?model=${encodeURIComponent(model)}`);
    if(!response.ok||!response.body)throw new Error(`Model pull failed (${response.status})`);
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',last={};
    while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const frames=buffer.split('\n\n');buffer=frames.pop()||'';
      for(const frame of frames){const line=frame.split('\n').find(x=>x.startsWith('data: '));if(!line)continue;let event;try{event=JSON.parse(line.slice(6));}catch{continue;}last=event;
        if(event.status==='error')throw new Error(event.error||'Ollama model pull failed');
        const pct=Number(event.percent);button.textContent=Number.isFinite(pct)&&pct>0?`Pulling ${model} · ${Math.min(100,Math.round(pct))}%`:`Pulling ${model}…`;
      }
    }
    if(last.status==='error')throw new Error(last.error||'Ollama model pull failed');
  }
  $('btn-connect-ollama').addEventListener('click',async()=>{const model=$('ollama-model-select').value,b=$('btn-connect-ollama');b.disabled=true;b.textContent='Checking Ollama…';try{
    const status=await api('/api/ollama/status');const installed=status.installed_models||[];
    const present=installed.some(x=>(typeof x==='string'?x:(x.name||x.model))===model);
    if(!present)await streamOllamaPull(model,b);
    b.textContent='Binding model…';const r=await post('/api/ollama/connect',{model});if(r.status==='error'||r.success===false)throw new Error(r.error||r.message||'Ollama connection failed');
    addModel('ollama',model,true);const k=`ollama::${model}`;if(!state.selected.includes(k))state.selected.unshift(k);saveSelected();selectedModelText();toast(r.message||`Connected to ${model}`,'success');await loadModels();
  }catch(e){toast(e.message,'error')}finally{b.disabled=false;b.textContent='Connect / pull selected local model';}});

  $('btn-launch-run').addEventListener('click',async()=>{
    const prompt=$('task-prompt').value.trim();if(!prompt){toast('Describe the task first.','error');$('task-prompt').focus();return;}
    let chosen=state.selected.map(findModel).filter(Boolean);
    if($('token-saving').checked){const firstCloud=chosen.findIndex(m=>m.provider!=='ollama');if(firstCloud>0){const [planner]=chosen.splice(firstCloud,1);chosen.unshift(planner);}const local=chosen.filter(m=>m.provider==='ollama');const rest=chosen.filter(m=>m.provider!=='ollama'&&m!==chosen[0]);chosen=[...chosen.slice(0,1),...local,...rest].filter((m,i,a)=>a.findIndex(x=>x.key===m.key)===i);}
    const selected=chosen.map(m=>({provider:m.provider,model:m.model}));
    const b=$('btn-launch-run');b.disabled=true;b.querySelector('span').textContent='Launching crew…';
    try{const r=await post('/api/crew/run',{prompt,agents:state.agentCount,mode:$('run-mode').value,token_saving:$('token-saving').checked,selected_models:selected,budget_usd:Number($('budget-usd').value||0),token_cap:Number($('token-cap').value||30000),planner_token_cap:Number($('planner-token-cap').value||5000)});if(r.status!=='accepted')throw new Error(r.error||'Could not launch crew');state.activeRun=r.run;toast('Crew accepted the mission. Live telemetry is now attached to the run.','success');$('task-prompt').value='';await refreshRuns();}
    catch(e){toast(e.message,'error')}finally{b.disabled=false;b.querySelector('span').textContent='Launch crew';}
  });
  $('btn-edit-active-run').addEventListener('click',editRun);
  $('btn-pause-run').addEventListener('click',async()=>{if(!state.activeRun)return;const action=state.activeRun.status==='pause_requested'||state.activeRun.status==='paused'?'resume':'pause';try{await post('/api/crew/control',{run_id:state.activeRun.id,action});await refreshRuns();toast(action==='pause'?'Pause requested. An in-flight model call may finish first.':'Crew resumed.');}catch(e){toast(e.message,'error')}});
  $('btn-stop-run').addEventListener('click',async()=>{if(!state.activeRun)return;try{await post('/api/crew/control',{run_id:state.activeRun.id,action:'stop'});await refreshRuns();toast('Stop requested. An in-flight provider call may not be instantly interruptible.');}catch(e){toast(e.message,'error')}});
  $('btn-refresh').addEventListener('click',refreshRuns);
  async function seatAction(seatId,action){if(!state.activeRun)return;let prompt='';if(action==='edit'){prompt=window.prompt('Inject a shared instruction into this agent’s live memory:');if(!prompt?.trim())return;}try{await post('/api/crew/control',{run_id:state.activeRun.id,seat_id:seatId,action,prompt});await refreshRuns();toast(`${seatId}: ${action} requested`,'success');}catch(e){toast(e.message,'error')}}
  async function editRun(){if(!state.activeRun)return;const prompt=window.prompt('Edit the shared task instruction. The update is injected into each seat; an already-issued request cannot be rewritten.',state.activeRun.prompt);if(!prompt?.trim())return;try{await post('/api/crew/control',{run_id:state.activeRun.id,action:'edit',prompt});await refreshRuns();toast('Shared instruction updated for the crew.','success')}catch(e){toast(e.message,'error')}}
  async function restartRun(){if(!state.activeRun)return;try{const r=await post('/api/crew/control',{run_id:state.activeRun.id,action:'restart',prompt:state.activeRun.prompt});if(r.status!=='accepted')throw new Error(r.error||'Restart failed');state.activeRun=r.run;await refreshRuns();toast('Restarted with a fresh run record.','success')}catch(e){toast(e.message,'error')}}

  async function refreshRuns(){
    if(state.polling)return;state.polling=true;
    try{
      const [data,status,cockpit]=await Promise.all([api('/api/crew/runs'),api('/api/status'),api('/api/agentufo/cockpit').catch(()=>({}))]);
      state.runs=data.runs||[];state.status=status;state.cockpit=cockpit;state.online=true;$('connection-dot').classList.add('online');$('statusbar-dot').classList.add('online');
      const current=state.runs.find(r=>!terminal(r.status))||state.runs[0]||null;
      // Merge live coordinator telemetry during synchronous model calls, when no progress callback fires.
      if(current&&!terminal(current.status)){
        const liveSeats=cockpit.seats||[];
        current.seats=(current.seats||[]).map((seat,i)=>{
          const live=liveSeats.find(x=>x.seat_id===seat.seat_id)||{};
          const liveTokens=Number(live.tokens_consumed||0);
          const streamingTokens=Number(live.live_tokens||0);
          const tokens=Math.max(liveTokens,streamingTokens,Number(seat.tokens||0));
          const liveModel=[live.assigned_provider,live.assigned_model].filter(Boolean).join(' / ');
          const statusValue=live.status||seat.status;
          const estimated=streamingTokens>liveTokens?false:Boolean(live.token_usage_estimated??seat.tokens_estimated);
          const inferredProgress=statusValue==='completed'?100:(statusValue==='working'||statusValue==='thinking'||statusValue==='generating'?Math.max(Number(seat.progress||0),i===0?22:(i===1?52:76)):Number(seat.progress||0));
          return {...seat,status:statusValue,tokens,tokens_estimated:estimated,model:liveModel||seat.model,last_event:live.current_thought||seat.last_event,progress:inferredProgress};
        });
        const total=current.seats.reduce((sum,seat)=>sum+Number(seat.tokens||0),0);
        if(total>0){current.tokens=total;current.tokens_estimated=current.seats.some(seat=>seat.tokens_estimated);}
      }
      // Prefer the active run; otherwise show the latest completed run for inspection.
      state.activeRun=current;renderRuns();renderChatRun(current);if(current&&terminal(current.status)&&!state.reportedChatRuns.has(current.id)){state.reportedChatRuns.add(current.id);if(current.result||current.error){const result=current.result||{};pushChat('assistant',`**Crew ${humanStatus(current.status)}**\n\n${current.error||result.message||result.content||'The crew finished without a text summary.'}${result.test_logs?`\n\n**Test output**\n\n\`\`\`\n${result.test_logs}\n\`\`\``:''}`,{model:'AgentUFO Crew',tokens:current.tokens||0,estimated:Boolean(current.tokens_estimated)});}}renderTimelineFromRuns();renderHeader(status,cockpit);if(activeUtilityWindow==='crew')renderCrewWindow();if(activeUtilityWindow==='control')refreshControlWindow();if($('view-settings').classList.contains('active'))renderSettings(status,cockpit);
    }catch(e){state.online=false;$('connection-dot').classList.remove('online');$('statusbar-dot').classList.remove('online');$('statusbar-text').textContent='Server unavailable — start AgentUFO locally';}
    finally{state.polling=false;}
  }
  function renderHeader(status,cockpit){
    const seats=cockpit.seats||[];const run=state.activeRun;
    $('metric-online').textContent=seats.length||status.swarm_agents||0;$('metric-online-foot').textContent=`${status.active_provider||'unconfigured'} · ${status.active_model||'no model'}`;
    $('metric-tokens').textContent=fmt(run?.tokens||0);$('metric-token-foot').textContent=run?(run.tokens_estimated?'Progress-based estimate · usage not reported yet':'Provider-reported or coordinator-reported usage'):'No active run';
    $('metric-status').textContent=run?humanStatus(run.status):'READY';$('metric-run-foot').textContent=run?`${run.agents} seats · ${time(run.updated_at)}`:'No task in flight';
    $('statusbar-text').textContent=status.provider_available?'AgentUFO server ready':'Server ready · provider setup may be required';$('statusbar-provider').textContent=`Provider: ${status.active_provider||'unconfigured'} / ${status.active_model||'none'}`;$('statusbar-root').textContent=`Workspace: ${(cockpit.workspace_root||status.workspace_root||'local').split(/[\\/]/).slice(-2).join('/')}`;
    $('workspace-label').textContent=(cockpit.workspace_root||'Local workspace').split(/[\\/]/).slice(-2).join('/');$('btn-pause-run').disabled=!run||terminal(run.status);$('btn-stop-run').disabled=!run||terminal(run.status);$('btn-edit-active-run').disabled=!run||terminal(run.status);
    $('btn-pause-run').textContent=run&&['pause_requested','paused'].includes(run.status)?'▶ Resume':'Ⅱ Pause';
  }
  function renderRuns(){
    const run=state.activeRun;const list=$('crew-list');
    if(!run){list.innerHTML='<div class="empty-state"><div class="empty-illustration">◈</div><strong>Your crew will appear here</strong><span>Live status, model assignment, activity, and token telemetry — in one place.</span></div>';$('active-run-title').textContent='No active mission';$('active-run-subtitle').textContent='Launch a task to populate live telemetry.';$('active-run-dot').classList.remove('active');$('run-result').hidden=true;return;}
    $('active-run-title').textContent=run.prompt||'Crew mission';$('active-run-subtitle').textContent=`${run.id} · ${run.mode} · ${time(run.created_at)}`;$('active-run-dot').classList.toggle('active',!terminal(run.status));
    list.innerHTML=(run.seats||[]).map((s,i)=>{
      const status=String(s.status||'queued').toLowerCase(), model=(run.selected_models||[])[i];const modelText=s.model|| (model?`${model.provider} / ${model.model}`:'Coordinator-selected model');
      return `<article class="agent-card"><div class="agent-top"><div class="agent-avatar">${esc((s.name||`A${i+1}`).slice(0,1))}</div><div class="agent-main"><strong>${esc(s.name||s.seat_id)}</strong><small>${esc(modelText)} · ${esc(s.last_event||'Waiting for activity')}</small></div><span class="state-pill ${esc(status)}">${esc(humanStatus(status))}</span></div><div class="agent-progress"><span style="width:${Math.max(0,Math.min(100,Number(s.progress)||0))}%"></span></div><div class="agent-meta"><span>TOKENS <b>${fmt(s.tokens||0)}${s.tokens_estimated?' est.':''}</b></span><span>PROGRESS <b>${Math.max(0,Math.min(100,Number(s.progress)||0))}%</b></span><span>${esc(s.seat_id)}</span></div><div class="agent-actions"><button data-seat-action="pause" data-seat="${esc(s.seat_id)}">Ⅱ Pause</button><button data-seat-action="resume" data-seat="${esc(s.seat_id)}">▶ Resume</button><button data-seat-action="edit" data-seat="${esc(s.seat_id)}">✎ Inject context</button><button data-seat-action="stop" data-seat="${esc(s.seat_id)}">■ Stop seat</button></div></article>`;
    }).join('');
    list.querySelectorAll('[data-seat-action]').forEach(b=>b.addEventListener('click',()=>seatAction(b.dataset.seat,b.dataset.seatAction)));
    $('run-result').hidden=!run.result&&!run.error;
    if(! $('run-result').hidden){const r=run.result||{};$('run-result').innerHTML=`<h3>${esc(run.error?'Run stopped with an error':`Result · ${humanStatus(run.status)}`)}</h3><p>${esc(run.error||r.message||r.content||'No summary returned.')}</p>${r.code?`<pre>${esc(r.code)}</pre>`:''}${r.test_logs?`<pre>${esc(r.test_logs)}</pre>`:''}${r.review?.diff_patch?`<h3>Reviewable diff</h3><pre>${esc(r.review.diff_patch)}</pre>`:''}<div class="agent-actions"><button id="btn-edit-run">✎ Edit shared task</button><button id="btn-restart-run">↻ Restart run</button></div>`;$('btn-edit-run')?.addEventListener('click',editRun);$('btn-restart-run')?.addEventListener('click',restartRun);}
  }
  function renderTimelineFromRuns(){
    const run=state.activeRun;const newEvents=run?.events||[];
    // Maintain a bounded client-side ledger across polling refreshes.
    for(const e of newEvents){const key=`${run.id}:${e.seq}`;if(!state.eventKeys)state.eventKeys=new Set();if(!state.eventKeys.has(key)){state.eventKeys.add(key);state.events.push({...e,run_id:run.id});}}
    if(state.events.length>300)state.events=state.events.slice(-300);renderTimeline();
  }
  function renderTimeline(){
    $('timeline-count').textContent=state.events.length;$('timeline-status').textContent=state.activeRun?`${humanStatus(state.activeRun.status)} · ${state.activeRun.id}`:'Waiting for activity';
    if(!state.events.length){$('timeline').innerHTML='<div class="timeline-empty">Events from every seat appear here. Token figures are labelled as estimates until the provider reports usage.</div>';return;}
    $('timeline').innerHTML=state.events.slice().reverse().map(e=>`<article class="timeline-item ${esc(e.kind||'')}" title="${esc(JSON.stringify(e.details||{}))}"><div class="timeline-time">${time(e.at)} · #${e.seq}</div><div class="timeline-title">${esc(e.message||e.kind)}</div><div class="timeline-detail">${esc(e.kind||'event')} · ${esc(e.run_id||'')}</div></article>`).join('');
  }

  async function loadTree(root){
    const query=root?`?dir=${encodeURIComponent(root)}`:'';$('file-tree').innerHTML='<div class="loading-line">Reading workspace files…</div>';
    try{const data=await api('/api/fs/tree'+query);if(data.status==='error'||data.error)throw new Error(data.error||'Could not read workspace');state.tree=data.tree;state.projectRoot=data.root_path;$('workspace-root').value=data.root_path;$('workspace-label').textContent=data.root_name||data.root_path.split(/[\\/]/).pop();renderTree(data.tree,0);$('statusbar-root').textContent=`Workspace: ${data.root_name||data.root_path}`;}
    catch(e){$('file-tree').innerHTML=`<div class="loading-line">${esc(e.message)}</div>`;}
  }
  function renderTree(node,depth){
    if(!node){$('file-tree').innerHTML='<div class="loading-line">No files returned.</div>';return;}
    const items=[];function walk(n,d){if(!n)return;if(!n.is_dir)items.push({node:n,depth:d});else{if(d>0)items.push({node:n,depth:d});(n.children||[]).forEach(c=>walk(c,d+1));}}walk(node,0);
    $('file-tree').innerHTML=items.length?items.map(({node:n,depth:d})=>`<button class="file-entry ${state.activeFile===n.path?'active':''}" data-file-path="${esc(n.path)}" style="padding-left:${10+d*13}px"><span class="file-icon">${n.is_dir?'▰':iconFor(n.ext)}</span><span>${esc(n.name)}</span></button>`).join(''):'<div class="loading-line">Workspace is empty or unreadable.</div>';
    $('file-tree').querySelectorAll('[data-file-path]').forEach(b=>b.addEventListener('click',()=>{const n=items.find(x=>x.node.path===b.dataset.filePath)?.node;if(n?.is_dir){loadTree(n.path);}else openFile(n.path);}));
  }
  function iconFor(ext){return ({'.py':'⌘','.js':'JS','.ts':'TS','.json':'{}','.md':'M','.html':'◇','.css':'#','.yml':'⚙','.yaml':'⚙','.toml':'⚙'})[ext]||'·';}
  async function openFile(path){try{const data=await api('/api/fs/read?path='+encodeURIComponent(path));if(data.status==='error'||data.error)throw new Error(data.error||'Could not read file');state.activeFile=data.path||path;$('editor-file-name').textContent=data.name||path.split(/[\\/]/).pop();$('file-editor').value=data.content||'';$('file-editor').disabled=false;$('btn-save-file').disabled=false;$('editor-status').textContent=`${data.lines||0} lines · ${data.size||0} bytes`;$('file-tree').querySelectorAll('.file-entry').forEach(b=>b.classList.toggle('active',b.dataset.filePath===state.activeFile));}catch(e){toast(e.message,'error');}}
  $('btn-load-files').addEventListener('click',()=>loadTree());$('btn-open-root').addEventListener('click',async()=>{const folder=$('workspace-root').value.trim();if(!folder)return;try{const r=await post('/api/projects/open',{folder_path:folder});if(r.status==='error'||r.error)throw new Error(r.error||'Could not open project');await loadTree();toast('Workspace opened.','success')}catch(e){toast(e.message,'error')}});
  $('btn-save-file').addEventListener('click',async()=>{if(!state.activeFile)return;try{const r=await post('/api/fs/write',{path:state.activeFile,content:$('file-editor').value});if(r.status==='error'||r.error)throw new Error(r.error||'Write failed');$('editor-status').textContent=`Saved · ${new Date().toLocaleTimeString()}`;await post('/api/agentufo/brief/handoff',{from_seat:'User Workspace Editor',to_seat:'all',title:'Workspace file changed',body:`File edited in AgentUFO UI: ${state.activeFile}. Re-read the latest version before continuing.` ,brief_type:'workspace_change'}).catch(()=>{});toast('Saved to disk. A workspace-change handoff was published.','success');await loadTree(state.projectRoot);}catch(e){toast(e.message,'error')}});

  async function loadSettings(){try{const [status,health,cockpit,locks,mailbox]=await Promise.all([api('/api/status'),api('/healthz'),api('/api/agentufo/cockpit').catch(()=>({})),api('/api/agentufo/locks').catch(()=>({})),api('/api/agentufo/mailbox').catch(()=>({}))]);renderSettings(status,cockpit,health,locks,mailbox);}catch(e){toast(e.message,'error')}}
  function renderSettings(status={},cockpit={},health={},locks={},mailbox={}){
    $('health-details').innerHTML=[['HTTP service',health.status||'unknown'],['Provider available',status.provider_available?'yes':'no'],['Setup required',health.setup_required?'yes':'no'],['Ollama',status.ollama?.online?'online':'not detected'],['Seats',cockpit.seats?.length||status.swarm_agents||0],['Workspace locks',locks.count||0]].map(([k,v])=>`<div class="health-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('');
    $('provider-details').innerHTML=[['Provider',status.active_provider||'unconfigured'],['Model',status.active_model||'none'],['Available',status.provider_available?'yes':'no'],['Token-saving mode',status.enable_hybrid?'enabled':'disabled'],['Selected fleet',state.selected.length],['Mailbox messages',mailbox.count||0]].map(([k,v])=>`<div class="health-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('');
    $('memory-details').innerHTML=[['Run ledger',`${state.runs.length} recent runs`],['Run storage','local atomic JSON ledger'],['Seat handoffs',`${mailbox.count||0} messages`],['Workspace',cockpit.workspace_root||'not reported']].map(([k,v])=>`<div class="health-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('');
  }
  $('btn-open-models-settings').addEventListener('click',()=>navigate('models'));
  // Chat-first interface. Conversation requests use the active model; build prompts enter the crew pipeline.
  function chatMarkdown(text){let s=esc(text);s=s.replace(/```([\w+-]*)\n([\s\S]*?)```/g,(_,lang,code)=>`<pre><code>${code.replace(/\n$/,'')}</code></pre>`);s=s.replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h2>$1</h2>').replace(/^# (.*)$/gm,'<h1>$1</h1>').replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>').replace(/^(?:- |\* )(.*)$/gm,'<li>$1</li>');s=s.replace(/(?:<li>.*<\/li>\n?)+/g,m=>`<ul>${m}</ul>`);return s.split(/\n{2,}/).map(b=>{const t=b.trim();if(!t||/^<(h[1-3]|pre|ul|ol|blockquote|div)/.test(t))return t;return `<p>${t.replace(/\n/g,'<br>')}</p>`}).join('')}
  function saveChatHistory(){try{localStorage.setItem('agentufo_chat_history_v1',JSON.stringify(state.chatMessages.slice(-100)))}catch{}}
  function renderChatMessages(){const host=$('chat-messages');if(!host)return;host.innerHTML=state.chatMessages.map((m,i)=>`<article class="chat-message ${m.role==='user'?'user':'assistant'}">${m.role==='assistant'?'<div class="chat-message-avatar">✳</div>':''}<div><div class="chat-message-body">${m.role==='user'?esc(m.content).replace(/\n/g,'<br>'):chatMarkdown(m.content||'')}</div>${m.role==='assistant'?`<div class="chat-message-meta"><span>${esc(m.model||'AgentUFO')}</span>${m.tokens?`<span>${fmt(m.tokens)} tokens${m.estimated?' · estimated':''}</span>`:''}${m.elapsed?`<span>${Number(m.elapsed).toFixed(1)}s</span>`:''}</div><div class="chat-message-actions"><button data-chat-copy="${i}">Copy</button></div>`:''}</div></article>`).join('');host.querySelectorAll('[data-chat-copy]').forEach(b=>b.addEventListener('click',()=>{const text=state.chatMessages[Number(b.dataset.chatCopy)]?.content||'';navigator.clipboard?.writeText(text).then(()=>toast('Copied response.','success')).catch(()=>toast('Clipboard unavailable.'))}));$('chat-welcome').hidden=state.chatMessages.length>0;host.scrollTop=host.scrollHeight;saveChatHistory()}
  function pushChat(role,content,meta={}){state.chatMessages.push({role,content,...meta});renderChatMessages()}
  function isBuildPrompt(text){return /\b(build|implement|create|write|generate|fix|refactor|debug|add tests|make a (?:website|web app|cli|tool|server|app)|modify (?:the )?(?:repo|repository|code|project|files))\b/i.test(text)&&!/\b(explain how to build|how do i build|what is building)\b/i.test(text)}
  function setChatSending(on){state.chatSending=on;$('chat-send').disabled=on;$('chat-send').textContent=on?'…':'↑';$('chat-prompt').disabled=on;}
  $('chat-active-model').addEventListener('change',async()=>{const model=findModel($('chat-active-model').value);if(!model)return;try{const r=await post('/api/settings',{provider:model.provider,model:model.model});if(r.status==='error'||r.error)throw new Error(r.error||'Could not select model');state.status=r;$('statusbar-provider').textContent=`Provider: ${model.provider} / ${model.model}`;toast(`Active chat model: ${model.model}`,'success')}catch(e){toast(e.message,'error')}});
  $('chat-open-routing').addEventListener('click',()=>openUtilityWindow('routing'));
  $('chat-open-files').addEventListener('click',()=>navigate('workspace'));
  $('chat-token-saving').addEventListener('click',()=>{state.chatTokenSaving=!state.chatTokenSaving;$('chat-token-saving').classList.toggle('active',state.chatTokenSaving);$('chat-token-saving').setAttribute('aria-pressed',String(state.chatTokenSaving));$('token-saving').checked=state.chatTokenSaving;$('window-token-saving').checked=state.chatTokenSaving;toast(state.chatTokenSaving?'Token-saving enabled for eligible crew tasks.':'Token-saving disabled.','info')});
  document.querySelectorAll('[data-chat-prompt]').forEach(b=>b.addEventListener('click',()=>{$('chat-prompt').value=b.dataset.chatPrompt;$('chat-prompt').focus()}));
  $('chat-prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendChatMessage()}});
  $('chat-prompt').addEventListener('input',()=>{const el=$('chat-prompt');el.style.height='auto';el.style.height=Math.min(180,Math.max(55,el.scrollHeight))+'px'});
  $('chat-send').addEventListener('click',sendChatMessage);
  async function sendChatMessage(){
    const input=$('chat-prompt'),prompt=input.value.trim();if(!prompt||state.chatSending)return;
    const model=findModel($('chat-active-model').value);if(model){try{const r=await post('/api/settings',{provider:model.provider,model:model.model});if(r.status==='error'||r.error)throw new Error(r.error||'Model selection failed')}catch(e){toast(`Could not activate selected model: ${e.message}`,'error');return}}
    pushChat('user',prompt);input.value='';input.style.height='auto';setChatSending(true);
    if(isBuildPrompt(prompt)){
      $('task-prompt').value=prompt;$('token-saving').checked=state.chatTokenSaving;
      // Keep existing budgets and selected fleet. A build request is routed to the verified crew pipeline.
      pushChat('assistant','**Crew request received.** I’m handing this to the configured Architect → Builder → Reviewer pipeline. You can follow each seat in Live crew and change the shared task while it runs.',{model:'AgentUFO Crew'});
      setChatSending(false);$('btn-launch-run').click();return;
    }
    const thinking={role:'assistant',content:'',model:model?`${model.provider} · ${model.model}`:'Active model',thinking:true};state.chatMessages.push(thinking);renderChatMessages();
    const thinkingNode=$('chat-messages').lastElementChild;
    if(thinkingNode){const body=thinkingNode.querySelector('.chat-message-body');if(body)body.innerHTML='<div class="chat-typing"><i></i><i></i><i></i></div><span style="color:var(--muted);font-size:11px">Thinking…</span>'}
    try{
      const response=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt,enable_hybrid:state.chatTokenSaving,swarm_mode:null})});
      if(!response.ok)throw new Error((await response.json().catch(()=>({}))).error||`Chat request failed (${response.status})`);if(!response.body)throw new Error('Streaming is unavailable in this browser');
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result=null,progress='';
      while(true){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const parts=buffer.split('\n\n');buffer=parts.pop()||'';for(const part of parts){const line=part.split('\n').find(x=>x.startsWith('data: '));if(!line)continue;let evt;try{evt=JSON.parse(line.slice(6))}catch{continue}if(evt.type==='progress'){progress=evt.event?.message||evt.event?.stage||'Working…';const body=$('chat-messages').lastElementChild?.querySelector('.chat-message-body');if(body)body.innerHTML=`<div class="chat-typing"><i></i><i></i><i></i></div><span style="color:var(--muted);font-size:11px">${esc(progress)}</span>`}else if(evt.type==='final')result=evt.data;else if(evt.type==='error')throw new Error(evt.error||'Model request failed')}}
      if(!result)throw new Error('The model connection ended without a final response.');const content=result.content||result.error||'The model returned an empty response.';state.chatMessages.pop();pushChat('assistant',content,{model:`${result.provider||model?.provider||'Model'} · ${result.model||model?.model||''}`,tokens:result.total_tokens_used||result.effective_tokens||result.thinking?.total_tokens_used||0,estimated:!result.token_usage_complete,elapsed:result.elapsed_seconds});
    }catch(e){state.chatMessages.pop();pushChat('assistant',`I couldn't complete that request: ${e.message}\n\nCheck that the selected provider is connected, then try again.`,{model:'AgentUFO · connection error'});toast(e.message,'error')}
    finally{setChatSending(false)}
  }
  function loadChatHistory(){try{const saved=JSON.parse(localStorage.getItem('agentufo_chat_history_v1')||'[]');if(Array.isArray(saved))state.chatMessages=saved.slice(-100)}catch{}renderChatMessages()}
  $('chat-run-edit').addEventListener('click',editRun);$('chat-run-pause').addEventListener('click',()=> $('btn-pause-run').click());$('chat-run-stop').addEventListener('click',()=> $('btn-stop-run').click());
  function renderChatRun(run){const box=$('chat-run-inline');if(!run){box.hidden=true;return}box.hidden=false;$('chat-run-title').textContent=(run.prompt||'Crew task').slice(0,90);$('chat-run-status').textContent=humanStatus(run.status);const seats=run.seats||[];const progress=seats.length?Math.round(seats.reduce((a,s)=>a+Number(s.progress||0),0)/seats.length):0;$('chat-run-progress-fill').style.width=`${progress|| (terminal(run.status)?100:4)}%`;$('chat-run-seats').innerHTML=seats.map(s=>`<span>${esc(s.name||s.seat_id)} · ${esc(s.status||'queued')} · ${fmt(s.tokens)} tokens${s.tokens_estimated?' est.':''}</span>`).join('');$('chat-run-pause').textContent=['paused','pause_requested'].includes(run.status)?'▶ Resume':'Ⅱ Pause';$('chat-run-pause').disabled=terminal(run.status);$('chat-run-stop').disabled=terminal(run.status)}

  async function init(){
    try{const health=await api('/healthz');state.online=true;$('connection-dot').classList.add('online');$('statusbar-dot').classList.add('online');$('statusbar-text').textContent=health.provider_available?'AgentUFO server ready':'Server ready · connect a model to execute tasks';}catch{$('statusbar-text').textContent='Cannot reach AgentUFO. Start run_web_ui.py and refresh.';}
    loadChatHistory(); await Promise.all([loadModels().catch(()=>{}),refreshRuns().catch(()=>{})]);
    setInterval(()=>refreshRuns(),1800);setInterval(()=>{if($('view-models').classList.contains('active'))loadModels().catch(()=>{});},30000);
  }
  window.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();navigate('models');}if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){if($('view-chat').classList.contains('active'))sendChatMessage();else if($('view-control').classList.contains('active'))$('btn-launch-run').click();}if(e.key==='Escape'&&document.activeElement===$('api-key'))$('api-key').blur();});


  // Floating utility windows are additive: the existing workspace and chat/task view is untouched.
  const windowBackdrop = $('utility-backdrop');
  let activeUtilityWindow = null;
  function openUtilityWindow(name) {
    const node = $(`window-${name}`); if (!node) return;
    closeUtilityWindow(); activeUtilityWindow = name;
    windowBackdrop.hidden = false; node.hidden = false;
    document.body.classList.add('utility-open');
    if (name === 'routing') { syncRoutingWindow(); if (!state.availableModels?.length) loadModels().then(syncRoutingWindow).catch(()=>{}); }
    if (name === 'crew') renderCrewWindow();
    if (name === 'control') refreshControlWindow();
    if (name === 'mcp') loadMcpPresets();
    node.querySelector('[data-close-window]')?.focus();
  }
  function closeUtilityWindow() {
    document.querySelectorAll('.utility-window').forEach(n => n.hidden = true);
    windowBackdrop.hidden = true; document.body.classList.remove('utility-open'); activeUtilityWindow = null;
  }
  document.querySelectorAll('[data-window]').forEach(b=>b.addEventListener('click',()=>openUtilityWindow(b.dataset.window)));
  document.querySelectorAll('[data-open-window]').forEach(b=>b.addEventListener('click',()=>openUtilityWindow(b.dataset.openWindow)));
  document.querySelectorAll('[data-close-window]').forEach(b=>b.addEventListener('click',closeUtilityWindow));
  windowBackdrop.addEventListener('click',closeUtilityWindow);
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&activeUtilityWindow)closeUtilityWindow();});

  function syncRoutingWindow() {
    const list=$('window-model-list'); if(!list)return;
    if(!state.availableModels?.length){list.innerHTML='<div class="loading-line">No catalog loaded yet. Refresh to probe available models.</div>';return;}
    const groups=[...new Set(state.availableModels.map(m=>m.provider))];
    list.innerHTML=groups.map(p=>`<div class="eyebrow window-provider-label">${esc(modelMeta[p]?.[0]||p)}</div>`+state.availableModels.filter(m=>m.provider===p).map(m=>`<label class="window-model-row"><input type="checkbox" data-window-model="${esc(m.key)}" ${state.selected.includes(m.key)?'checked':''}><span><strong>${esc(m.model)}</strong><small>${esc(p)} · ${m.ready?'detected':'not verified'}</small></span><span class="window-model-state ${m.ready?'ready':''}">${m.ready?'READY':'CHECK'}</span></label>`).join('')).join('');
    list.querySelectorAll('[data-window-model]').forEach(input=>input.addEventListener('change',()=>{
      const key=input.dataset.windowModel;
      if(input.checked){if(state.selected.length>=3){input.checked=false;toast('This verified pipeline supports up to three model assignments.','error');return;}if(!state.selected.includes(key))state.selected.push(key);}
      else state.selected=state.selected.filter(k=>k!==key);
      saveSelected();renderModels();selectedModelText();syncRoutingWindow();
    }));
    $('window-token-saving').checked=$('token-saving').checked;
    $('window-token-cap').value=$('token-cap').value;$('window-planner-cap').value=$('planner-token-cap').value;$('window-budget-usd').value=$('budget-usd').value;
  }
  $('window-refresh-models').addEventListener('click',async()=>{await loadModels();syncRoutingWindow();});
  $('window-apply-routing').addEventListener('click',async()=>{
    $('token-saving').checked=$('window-token-saving').checked;state.chatTokenSaving=$('window-token-saving').checked;$('chat-token-saving').classList.toggle('active',state.chatTokenSaving);$('chat-token-saving').setAttribute('aria-pressed',String(state.chatTokenSaving));$('token-cap').value=$('window-token-cap').value;$('planner-token-cap').value=$('window-planner-cap').value;$('budget-usd').value=$('window-budget-usd').value;
    const selected=state.selected.map(findModel).filter(Boolean).map(m=>({provider:m.provider,model:m.model}));
    try{await post('/api/crew/configure',{multi_llm_fleet:selected.map((m,i)=>({...m,id:`${m.provider}-${m.model}`,name:`${m.provider} / ${m.model}`,role:['Architect','Builder','Reviewer'][i],active:true})),num_seats:3,token_saving:$('window-token-saving').checked});toast(`Routing applied to future runs (${selected.length} model(s)).`,'success');closeUtilityWindow();}
    catch(e){toast(e.message,'error');}
  });

  function renderWindowMetrics(container, pairs) { const el=$(container);if(!el)return;el.innerHTML=pairs.map(([label,value,sub])=>`<article class="window-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(sub||'')}</small></article>`).join(''); }
  function renderCrewWindow() {
    const run=state.activeRun, seats=run?.seats||[];
    $('window-crew-subtitle').textContent=run?`${run.id} · ${humanStatus(run.status)} · updates automatically`:'No active mission';
    renderWindowMetrics('window-crew-metrics', [['SEATS',seats.length, 'configured'],['TOKENS',fmt(run?.tokens||0),run?.tokens_estimated?'estimated':'provider reported'],['STATUS',run?humanStatus(run.status):'READY',run?time(run.updated_at):'No active run']]);
    const list=$('window-crew-list');
    if(!run){list.innerHTML='<div class="empty-state">No mission yet. Launch a task from the current screen.</div>';return;}
    list.innerHTML=seats.map((seat,i)=>`<article class="window-seat"><div class="window-seat-top"><div><strong>${esc(seat.name||seat.seat_id)}</strong><small>${esc(seat.model||((run.selected_models||[])[i]?.model)||'Model pending')} · ${esc(seat.last_event||'Waiting')}</small></div><span class="state-pill ${esc(String(seat.status||'queued').toLowerCase())}">${esc(humanStatus(seat.status))}</span></div><div class="agent-progress"><span style="width:${Math.max(0,Math.min(100,Number(seat.progress)||0))}%"></span></div><div class="agent-meta"><span>TOKENS <b>${fmt(seat.tokens)}${seat.tokens_estimated?' est.':''}</b></span><span>PROGRESS <b>${Math.max(0,Math.min(100,Number(seat.progress)||0))}%</b></span></div><div class="agent-actions"><button data-window-seat="pause" data-seat="${esc(seat.seat_id)}">Pause</button><button data-window-seat="resume" data-seat="${esc(seat.seat_id)}">Resume</button><button data-window-seat="edit" data-seat="${esc(seat.seat_id)}">Inject context</button><button data-window-seat="stop" data-seat="${esc(seat.seat_id)}">Stop seat</button></div></article>`).join('');
    list.querySelectorAll('[data-window-seat]').forEach(b=>b.addEventListener('click',()=>seatAction(b.dataset.seat,b.dataset.windowSeat)));
  }
  $('window-edit-task').addEventListener('click',editRun);
  $('window-pause-all').addEventListener('click',()=> $('btn-pause-run').click());
  $('window-stop-all').addEventListener('click',()=> $('btn-stop-run').click());
  $('window-refresh-crew').addEventListener('click',refreshRuns);
  async function refreshControlWindow() {
    const run=state.activeRun, status=state.status||{};
    renderWindowMetrics('window-control-metrics', [['CREW ONLINE',state.cockpit?.seats?.length||3,'configured seats'],['TOKENS',fmt(run?.tokens||0),run?.tokens_estimated?'estimated':'reported'],['RUN',run?humanStatus(run.status):'READY',run?.id||'No active mission'],['MODELS',state.selected.length,'selected for routing']]);
    $('window-control-active').innerHTML=run?`<strong>${esc(run.prompt||'Crew mission')}</strong><p class="muted-copy">${esc(run.id)} · ${esc(humanStatus(run.status))}</p><p>${esc(run.error||run.result?.content||'Mission in progress or ready for launch.')}</p>`:'No active mission. Use the main screen to start one.';
    try{const [health,cockpit]=await Promise.all([api('/healthz'),api('/api/agentufo/cockpit').catch(()=>({}))]);state.cockpit=cockpit;$('window-control-health').innerHTML=[['Server',health.status||'ready'],['Provider',status.active_provider||'unconfigured'],['Model',status.active_model||'none'],['Ollama',status.ollama?.online?'online':'not detected'],['Workspace',cockpit.workspace_root||'local'],['Local models',state.availableModels?.filter(m=>m.provider==='ollama').length||0]].map(([k,v])=>`<div class="health-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join('');}
    catch(e){$('window-control-health').textContent=`Health check failed: ${e.message}`;}
  }
  $('window-control-refresh').addEventListener('click',refreshControlWindow);

  let generatedMcp = null;
  async function loadMcpPresets(){try{await api('/api/mcp/presets');}catch(e){$('mcp-result').textContent=`MCP presets unavailable: ${e.message}`;}}
  function readJsonField(id, expected, label){let v;try{v=JSON.parse($(id).value||expected);}catch{throw new Error(`${label} must be valid JSON.`);}if(v===null||typeof v!==typeof expected)throw new Error(`${label} has the wrong JSON type.`);return v;}
  function mcpPayload(){const params=readJsonField('mcp-tool-parameters',[],'Parameters');if(!Array.isArray(params))throw new Error('Parameters must be a JSON array.');return {server_name:$('mcp-server-name').value.trim(),description:$('mcp-server-description').value.trim(),tools:[{name:$('mcp-tool-name').value.trim(),description:$('mcp-tool-description').value.trim(),parameters:params,handler_code:$('mcp-tool-handler').value}]};}
  $('mcp-generate').addEventListener('click',async()=>{const b=$('mcp-generate');b.disabled=true;$('mcp-result').textContent='Generating server and connection config…';try{const result=await post('/api/mcp/generate',mcpPayload());if(result.status!=='success')throw new Error(result.error||'Generation failed');generatedMcp=result;$('mcp-output').value=`# Generated MCP server\n\n${result.python_code||''}\n\n# Client configuration\n${typeof result.claude_desktop_config==='string'?result.claude_desktop_config:JSON.stringify(result.claude_desktop_config||{},null,2)}`;$('mcp-save-project').disabled=false;$('mcp-result').textContent='Server source and client configuration generated. Save them into the active project before connecting a trusted MCP host.';toast('MCP server generated.','success');}catch(e){$('mcp-result').textContent=e.message;toast(e.message,'error');}finally{b.disabled=false;}});
  $('mcp-test').addEventListener('click',async()=>{const b=$('mcp-test');b.disabled=true;try{const payload=mcpPayload();const args=readJsonField('mcp-tool-arguments',{},'Test arguments');const result=await post('/api/mcp/test',{tool:payload.tools[0],arguments:args});$('mcp-output').value=JSON.stringify(result,null,2);const passed=result.status==='success';$('mcp-result').textContent=passed?'Tool test passed.':`Tool test did not pass: ${result.stderr||result.error||'inspect output'}`;toast(passed?'MCP tool test passed':'MCP tool test failed',passed?'success':'error');}catch(e){$('mcp-result').textContent=e.message;toast(e.message,'error');}finally{b.disabled=false;}});
  $('mcp-save-project').addEventListener('click',async()=>{if(!generatedMcp)return;const b=$('mcp-save-project');b.disabled=true;try{let cfg=generatedMcp.claude_desktop_config;if(typeof cfg==='string'){try{cfg=JSON.parse(cfg)}catch{throw new Error('Generated client config is not valid JSON.')}}const server=await post('/api/fs/write',{path:'agentufo_mcp/server.py',content:generatedMcp.python_code||''});if(server.status!=='success')throw new Error(server.error||'Could not write MCP server');const config=await post('/api/fs/write',{path:'agentufo_mcp/client_config.json',content:JSON.stringify(cfg,null,2)});if(config.status!=='success')throw new Error(config.error||'Could not write MCP client config');$('mcp-result').textContent=`Saved server and config to the active workspace: ${server.path}; ${config.path}. Install the MCP SDK in the environment used by the MCP host before launching.`;toast('MCP server and client config saved to the project.','success');await loadTree(state.projectRoot).catch(()=>{});}catch(e){$('mcp-result').textContent=e.message;toast(e.message,'error');}finally{b.disabled=false;}});
  $('mcp-copy-output').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('mcp-output').value);toast('MCP output copied.','success');}catch{ $('mcp-output').select();document.execCommand('copy');toast('MCP output selected/copied if clipboard access is allowed.');}});

  init();
})();
