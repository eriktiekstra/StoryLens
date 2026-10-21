"""Token-protected loopback browser frontend for the shared wizard session."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

from .config import Config
from .wizard_session import WizardSession

_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StoryLens · A little clarity for your media</title>
<style>
:root{color-scheme:light;--ink:#18362e;--muted:#5b6e66;--paper:#f4f3ec;--card:#fffefa;--green:#27634c;--line:#d6dfd4;--red:#a13229;--soft:#edf2e8;--gold:#f5e7c8}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:1060px;margin:auto;padding:36px 26px 80px}.brand{text-transform:uppercase;letter-spacing:.14em;font-size:12px;font-weight:800;display:flex;justify-content:space-between;gap:16px}.brand span{letter-spacing:0;text-transform:none;color:var(--muted);font-weight:500}header{padding:40px 0 24px}h1{font:normal clamp(38px,6vw,64px)/1.04 Georgia,serif;letter-spacing:-.04em;max-width:760px;margin:0 0 18px}header p{font-size:18px;color:var(--muted);max-width:670px;margin:0}h2{font-size:23px;letter-spacing:-.025em;margin:0 0 8px}h3{font-size:17px;margin:0 0 8px}p{margin:8px 0 14px}.muted,.help{color:var(--muted)}.help{font-size:14px}.steps{display:flex;gap:8px;flex-wrap:wrap;padding:0;margin:24px 0;list-style:none}.steps a{display:block;color:var(--muted);border-bottom:2px solid var(--line);padding:8px 12px;text-decoration:none}.steps a[aria-current=step]{border-color:var(--green);color:var(--ink);font-weight:700}.card{border:1px solid var(--line);border-radius:20px;background:var(--card);padding:28px;margin:18px 0;box-shadow:0 4px 20px #17372d04}.heading{display:flex;gap:16px;align-items:start;margin-bottom:20px}.num{flex:none;display:grid;place-items:center;border:1px solid var(--line);background:var(--soft);border-radius:50%;width:34px;height:34px;font-weight:700}.heading p{margin:0}.fields{display:grid;grid-template-columns:1fr 1fr;gap:16px}.full{grid-column:1/-1}label{font-weight:650;font-size:14px;display:block;margin-bottom:6px}input,select{width:100%;font:inherit;padding:11px 12px;border:1px solid #a5b5a8;border-radius:9px;background:white;color:var(--ink)}input[type=checkbox]{width:18px;height:18px;flex:none;margin:3px 0 0;accent-color:var(--green)}.path-row{display:flex;gap:8px}.path-row input{min-width:0}.actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:20px}button,.button{border:1px solid var(--green);border-radius:9px;padding:10px 16px;background:var(--green);color:white;font:650 14px/1.5 system-ui;cursor:pointer;text-decoration:none;display:inline-block}button.secondary,.button.secondary{color:var(--green);background:transparent}button.danger{background:white;color:var(--red);border-color:var(--red)}button:disabled{cursor:default;opacity:.5}:focus-visible{outline:3px solid #bb821b;outline-offset:4px}.hidden,[hidden]{display:none!important}.notice{background:var(--soft);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:16px 0;white-space:pre-wrap;overflow-wrap:anywhere}.notice.warn{background:var(--gold);border-color:#e3cda0}.error{color:var(--red);white-space:pre-wrap;overflow-wrap:anywhere}.error:empty{display:none}.pill{font-size:13px;border:1px solid var(--line);background:var(--soft);border-radius:30px;padding:5px 12px;display:inline-block;margin:4px 6px 4px 0}.status-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.status-box{border:1px solid var(--line);border-radius:12px;padding:18px}.status-box strong{display:block;font-size:18px}.status-box p{font-size:14px;margin-bottom:0}.activity{border-left:3px solid var(--green);padding:6px 14px;margin:16px 0;overflow-wrap:anywhere}.check{display:flex;align-items:start;gap:10px;font-weight:400;margin:14px 0}.check strong{display:block}.check span{flex:1}.capabilities{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.capability{border:1px solid var(--line);border-radius:12px;padding:14px}.capability label{margin-top:0}.capability small{display:block;color:var(--muted);margin-top:4px}.capability button{font-size:13px;padding:8px 10px;margin-top:8px}details{border-top:1px solid var(--line);margin-top:20px;padding-top:14px}summary{cursor:pointer;font-weight:650;margin-bottom:14px}pre{font:13px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--soft);padding:16px;border-radius:10px;max-height:420px;overflow:auto}.result-prompt{white-space:pre-wrap;max-height:400px;overflow:auto;border:1px solid var(--line);border-radius:10px;padding:18px;font-size:14px}.errors{padding-left:22px}.errors li{margin:12px 0;overflow-wrap:anywhere}.errors code{font-size:13px}.footer{font-size:13px;color:var(--muted);max-width:800px;margin-top:32px}dialog{width:min(680px,calc(100% - 32px));border:1px solid var(--line);border-radius:18px;padding:24px;background:var(--card);color:var(--ink)}dialog::backdrop{background:#122c2470}.directory-list{list-style:none;padding:0;max-height:320px;overflow:auto;border-block:1px solid var(--line)}.directory-list button{display:block;width:100%;text-align:left;border:none;border-radius:0;background:transparent;color:var(--green);padding:12px}.directory-list li+li{border-top:1px solid var(--line)}.directory-list button:hover{background:var(--soft)}#busy-note{font-size:14px;color:var(--muted)}a{color:var(--green)}@media(max-width:700px){main{padding:24px 16px 50px}.card{padding:20px}.fields,.status-grid,.capabilities{grid-template-columns:1fr}.brand{flex-direction:column;gap:4px}.heading{gap:10px}.steps a{padding:8px;font-size:13px}.path-row{flex-wrap:wrap}.path-row input{flex-basis:100%}h2{font-size:21px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style></head><body><main>
<div class="brand">StoryLens <span>On your computer. Your source files stay untouched.</span></div>
<header><h1>A little clarity.<br>A story waiting inside.</h1><p>Turn a folder of photos, video and audio into a useful local catalog. Start with the basics; add local AI only when you want it.</p></header>
<nav aria-label="Workflow"><ol class="steps"><li><a href="#choose" id="nav-choose" aria-current="step">1 · Choose</a></li><li><a href="#readiness" id="nav-readiness">2 · Get ready</a></li><li><a href="#analysis" id="nav-analysis">3 · Analyze</a></li><li><a href="#results" id="nav-results">4 · Use your results</a></li></ol></nav>
<div id="error" class="error" role="alert" tabindex="-1"></div><p id="busy-note" role="status" aria-live="polite"></p>
<section id="choose" class="card" aria-labelledby="choose-title"><div class="heading"><span class="num" aria-hidden="true">1</span><div><h2 id="choose-title">Choose your folders</h2><p class="muted">No upload, no copy. Pick a media folder and a separate place for the catalog.</p></div></div>
<form id="project-form"><div class="fields"><div class="full"><label for="source">Media folder</label><div class="path-row"><input id="source" autocomplete="off" placeholder="/path/to/photos-and-videos"><button type="button" class="secondary browse" data-target="source">Browse media</button></div><p class="help">For a saved project, leave this blank to use its original source folder.</p></div><div class="full"><label for="project">StoryLens project folder</label><div class="path-row"><input id="project" required autocomplete="off" placeholder="/path/to/storylens-project"><button type="button" class="secondary browse" data-target="project">Browse projects</button></div><p class="help">Open an existing StoryLens project, or type a new folder name outside your media folder.</p></div></div><div class="actions"><button id="start" type="submit">Open or create project</button><span id="open-status" role="status"></span></div></form></section>
<section id="readiness" class="card hidden" aria-labelledby="readiness-title"><div class="heading"><span class="num" aria-hidden="true">2</span><div><h2 id="readiness-title">A quick readiness check</h2><p class="muted">Technical analysis works without AI. Optional tools unlock more kinds of media.</p></div></div><div id="project-path" class="help"></div><div id="media-counts"></div><div id="readiness-notice" class="notice"></div><div id="readiness-fixes"></div><div class="actions"><button id="refresh-readiness" class="secondary">Recheck readiness</button><a href="#analysis" class="button">Continue to analysis</a></div>
<details id="ai-setup"><summary>Optional · Give this project local AI capabilities</summary><p>Keep the technical catalog and add descriptions, search, or speech later. Nothing is enabled just by opening this panel. AI runs locally; model installation is a separate, explicit download.</p><div class="actions"><button id="detect-ai" class="secondary">Detect local setup</button><button id="load-profile" class="secondary">Reuse saved AI profile</button></div><p id="ai-detected" class="help" role="status"></p>
<div class="capabilities"><div class="capability"><label class="check"><input id="enable-vision" type="checkbox"><span><strong>Describe what is visible</strong><small>Local vision model describes images and sampled video frames. Adds context, not creative decisions.</small></span></label><p id="recommend-vision" class="help"></p><button class="secondary download" data-capability="vision">Get recommended vision model</button></div><div class="capability"><label class="check"><input id="enable-embedding" type="checkbox"><span><strong>Find related moments</strong><small>Local image embeddings support similarity and semantic search.</small></span></label><p id="recommend-embedding" class="help"></p><button class="secondary download" data-capability="embedding">Get recommended search model</button></div><div class="capability"><label class="check"><input id="enable-transcription" type="checkbox"><span><strong>Read spoken words</strong><small>Local speech recognition turns supported audio into searchable text.</small></span></label><p id="recommend-transcription" class="help"></p><button class="secondary download" data-capability="transcription">Get recommended speech model</button></div></div>
<div id="ai-dependencies"></div><details><summary>Advanced · Model names, paths and limits</summary><div class="fields"><div><label for="vision_model">Ollama vision model name</label><input id="vision_model" placeholder="Installed local model name"></div><div><label for="ollama_endpoint">Local Ollama endpoint</label><input id="ollama_endpoint" value="http://127.0.0.1:11434"></div><div><label for="embedding_model">Image embedding model folder</label><input id="embedding_model" placeholder="/path/to/local/model"></div><div><label for="audio_transcription_model">Speech model folder</label><input id="audio_transcription_model" placeholder="/path/to/local/model"></div><div><label for="audio_transcription_device">Speech device</label><select id="audio_transcription_device"><option value="cpu">CPU (works on most computers)</option><option value="cuda">NVIDIA GPU (CUDA required)</option></select></div><div><label for="audio_transcription_language">Speech language code (blank = detect)</label><input id="audio_transcription_language" placeholder="e.g. en"></div><div><label for="timeout">Technical timeout per file (seconds)</label><input id="timeout" type="number" min="1" value="600"></div><div><label for="semantic_timeout">AI timeout per task (seconds)</label><input id="semantic_timeout" type="number" min="1" value="120"></div></div></details>
<div class="actions"><button id="save-limits" class="secondary">Save timeout limits only</button></div><p class="help">Timeout changes do not enable AI or start analysis. These limits also apply when retrying technical analysis.</p>
<div class="actions"><button id="check-ai" class="secondary">1 · Check setup</button><button id="sample-ai" class="secondary" disabled>2 · Test on a sample</button><button id="save-ai" disabled>3 · Save AI setup</button><button id="save-profile" class="secondary">Save reusable profile</button></div><p class="help">Checking does not process your media. Testing explicitly runs local inference on a small sample; it does not analyze the whole folder. Saving a profile is a separate action and never enables AI in other projects automatically.</p><div id="ai-feedback" class="notice hidden" role="status" aria-live="polite"></div><div class="actions"><button id="technical-only" class="secondary">Use technical analysis only</button></div></details></section>
<section id="analysis" class="card hidden" aria-labelledby="analysis-title"><div class="heading"><span class="num" aria-hidden="true">3</span><div><h2 id="analysis-title">Analyze at your pace</h2><p class="muted">Start with metadata and technical signals. Successful work is cached so a retry can pick up where you left off.</p></div></div><div id="run-status" class="activity" role="status" aria-live="polite">Ready when you are.</div><div id="run-counts"></div><p class="help">Indexing finds files; it is not analysis completion. Counts are real observations, not a guessed percentage.</p><div class="actions"><button id="analyze">Run technical analysis</button><button id="enrich" class="secondary" disabled>Run local AI enrichment</button><button id="cancel" class="danger" hidden>Stop safely</button></div><p class="help">Stopping preserves completed work. Reopen this project to resume, retry failed files, or add AI without rebuilding the technical catalog.</p></section>
<section id="results" class="card hidden" aria-labelledby="results-title"><div class="heading"><span class="num" aria-hidden="true">4</span><div><h2 id="results-title" tabindex="-1">Your catalog, ready to use</h2><p class="muted">Technical facts and optional AI evidence are tracked separately.</p></div></div><div class="status-grid"><div class="status-box"><h3>Technical analysis</h3><strong id="technical-status">Not started</strong><p id="technical-summary"></p></div><div class="status-box"><h3>Local AI enrichment</h3><strong id="ai-status">Not enabled</strong><p id="ai-summary"></p></div></div><div id="result-notice" class="notice"></div><div id="file-errors"></div><div class="actions"><button id="retry" class="secondary">Retry incomplete analysis</button><button id="add-ai" class="secondary">Add or adjust local AI</button><button id="diagnostics" class="secondary">Save diagnostic report</button><button id="open-folder" class="secondary">Open project folder</button></div><div id="diagnostic-result" class="help" role="status"></div>
<details id="handoff"><summary>Optional · Continue with your existing FilmCraft agent</summary><p>StoryLens supplies evidence. Your existing agent makes creative choices and uses its configured FilmCraft MCP tools. StoryLens does not connect to your AI agent or FilmCraft.</p><ol><li>Install the optional MCP dependency in this StoryLens environment, if needed.</li><li>Add the local StoryLens server to a trusted MCP host that already has FilmCraft configured.</li><li>Copy the prompt below into that agent. Review its plan and every export.</li></ol><div id="handoff-instructions" class="notice"></div><label class="check"><input type="checkbox" id="inspect"><span><strong>Allow my agent to inspect the original media</strong>This prompt does not launch the agent or transfer files. If your agent uses a cloud provider, approve media sharing with that provider separately. This checkbox changes the prompt, not filesystem access permissions.</span></label><div id="prompt" class="result-prompt" tabindex="0"></div><div class="actions"><button id="copy" class="secondary">Copy handoff prompt</button><span id="copied" role="status"></span></div><details><summary>Advanced · MCP configuration JSON</summary><pre id="mcp"></pre></details></details></section>
<p class="footer">Private by design: this page is served only on this computer, with a session token. Media stays local during analysis. Model downloads contact their model host only after your explicit approval; StoryLens does not upload source media. Keep the terminal open while using this page.</p>
<dialog id="folder-dialog" aria-labelledby="folder-title"><h2 id="folder-title">Choose a local folder</h2><p class="help">Browse directories on this computer. Nothing is uploaded.</p><form id="browse-form"><label for="browse-path">Folder path</label><div class="path-row"><input id="browse-path" autocomplete="off"><button type="submit" class="secondary">Go</button></div></form><div class="actions"><button id="browse-up" class="secondary">Parent folder</button></div><div id="browse-error" class="error" role="alert"></div><ul id="directories" class="directory-list"></ul><p id="browse-note" class="help"></p><div class="actions"><button id="browse-select">Use this folder</button><button id="browse-close" class="secondary">Cancel</button></div></dialog>
<dialog id="download-dialog" aria-labelledby="download-title"><h2 id="download-title">Download a local AI model?</h2><p id="download-description"></p><div class="notice warn">This is an optional network download, not an upload. It can take time and use several GB of storage. Check that you have enough disk space. Source photos, video and audio are not sent to the model host.</div><label class="check"><input type="checkbox" id="download-consent"><span>I approve this model download and its local storage use.</span></label><div class="actions"><button id="download-confirm" disabled>Download model</button><button id="download-close" class="secondary">Not now</button></div></dialog>
<script>
'use strict';
const $=id=>document.getElementById(id), token=location.hash.slice(1), params=new URLSearchParams(location.search);
let state=null, options=null, timer=null, pending=false, folderTarget='', folderParent='', capability='', checkPassed=false, samplePassed=false;
const textFields=['embedding_model','vision_model','ollama_endpoint','audio_transcription_model','audio_transcription_device','audio_transcription_language'];
const numberFields=['timeout','semantic_timeout'];
$('source').value=params.get('source')||'';$('project').value=params.get('project')||'';
async function api(path,data){const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data),cache:'no-store'});let value;try{value=await response.json()}catch{throw Error('The local server did not return a response. Keep the StoryLens terminal open, then refresh this page.')}if(!response.ok)throw Error(value.error||'The local request failed');return value}
function showError(error){$('error').textContent=error?error.message||String(error):'';if(error)$('error').focus()}
function lines(value){if(Array.isArray(value))return value.map(v=>typeof v==='string'?v:JSON.stringify(v)).join('\n');return typeof value==='string'?value:JSON.stringify(value,null,2)}
function notice(id,value){$(id).textContent=value||'';$(id).classList.toggle('hidden',!value)}
function statusName(value){return ({not_started:'Not started',ready:'Ready',running:'Running',completed:'Complete',partial:'Partially complete',failed:'Needs attention',cancelled:'Stopped safely',interrupted:'Interrupted',disabled:'Not enabled',cancel_requested:'Stopping safely'})[value]||value||'Not started'}
function activeStep(id){for(const step of ['choose','readiness','analysis','results']){const nav=$('nav-'+step);if(step===id)nav.setAttribute('aria-current','step');else nav.removeAttribute('aria-current')}}
function flags(){const busy=pending||!!(state&&state.busy);document.querySelectorAll('button').forEach(button=>{if(!['browse-close','download-close'].includes(button.id))button.disabled=busy});document.querySelectorAll('input,select').forEach(input=>input.disabled=busy);$('cancel').disabled=!(state&&state.busy&&state.status==='running')||pending;$('sample-ai').disabled=busy||!checkPassed;$('save-ai').disabled=busy||!samplePassed;$('download-confirm').disabled=busy||!$('download-consent').checked;const project=state&&state.project,settings=project&&project.settings||{};$('enrich').disabled=busy||!project||project.mode!=='local-ai'||!(settings.embedding_model||settings.vision_model||settings.audio_transcription_model);const needsRetry=state&&[state.technical,state.ai].some(result=>result&&['partial','failed','interrupted'].includes(result.status));$('retry').hidden=!needsRetry;$('retry').disabled=busy||!project||!needsRetry}
async function action(label,work){if(pending)return;pending=true;showError(null);$('busy-note').textContent=label;flags();schedulePoll();try{return await work()}catch(error){showError(error)}finally{pending=false;$('busy-note').textContent='';try{render(await api('/api/state'))}catch(error){showError(error)}flags()}}
function countPills(id,items){const root=$(id);root.replaceChildren();for(const [label,value] of items){const pill=document.createElement('span');pill.className='pill';pill.textContent=label+' · '+value;root.append(pill)}}
function describe(summary){if(!summary||!Object.keys(summary).length)return 'No saved results yet.';return `${summary.discovered||0} discovered · ${summary.indexed||0} indexed · ${summary.analyzed||0} analyzed · ${summary.cache_hits||0} cached · ${summary.failed||0} failed`}
function applySettings(settings){for(const name of [...textFields,...numberFields])if(settings[name]!==undefined)$(name).value=settings[name];$('enable-vision').checked=!!settings.vision_model;$('enable-embedding').checked=!!settings.embedding_model;$('enable-transcription').checked=!!settings.audio_transcription_model;invalidateAI()}
function render(snapshot){
  state=snapshot;
  const project=state.project;
  if(!project){flags();if(state.busy)schedulePoll();return}
  for(const id of ['readiness','analysis'])$(id).classList.remove('hidden');
  $('ai-setup').hidden=false;document.querySelector('a[href="#analysis"].button').hidden=false;
  $('source').value=project.source||'';
  $('project').value=project.directory||'';
  $('project-path').textContent='Project: '+project.directory;
  countPills('media-counts',Object.entries(state.counts||{}));
  const warnings=state.warnings||[];
  notice('readiness-notice',warnings.length?lines(warnings):'Your project is open. Technical analysis needs no AI models.');
  $('readiness-notice').classList.toggle('warn',warnings.length>0);
  $('readiness-fixes').replaceChildren();
  if((state.installation_instructions||[]).length){
    const details=document.createElement('details'),heading=document.createElement('summary'),pre=document.createElement('pre');
    heading.textContent='Installation guidance for optional or missing tools';
    pre.textContent=lines(state.installation_instructions);
    details.append(heading,pre);$('readiness-fixes').append(details);
  }
  const summary=state.summary||{};
  countPills('run-counts',Object.entries(summary).filter(([key,value])=>['discovered','indexed','analyzed','cache_hits','failed','skipped'].includes(key)&&typeof value==='number'));
  const activity=typeof state.activity==='object'?lines(state.activity):state.activity;
  const seconds=Math.max(0,Math.floor(Number(state.elapsed)||0)),analyzing=state.busy&&state.status==='running';
  $('run-status').textContent=[statusName(state.status),analyzing?`${Math.floor(seconds/60)}m ${seconds%60}s elapsed`:'',activity||''].filter(Boolean).join(' · ');
  $('cancel').hidden=!analyzing;
  const technical=state.technical||{},ai=state.ai||{};
  $('analyze').textContent=technical.status==='completed'?'Check for new or changed media':technical.status&&technical.status!=='not_started'?'Resume / retry basic analysis':'Start basic analysis';
  $('technical-status').textContent=statusName(technical.status);
  $('technical-summary').textContent=describe(technical.summary);
  $('ai-status').textContent=ai.status==='not_started'&&project.mode==='technical'?'Not enabled':statusName(ai.status);
  $('ai-summary').textContent=describe(ai.summary);
  const hasResults=[technical,ai].some(result=>(result.summary&&Object.keys(result.summary).length)||!['not_started','disabled',undefined,null].includes(result.status));
  $('results').classList.toggle('hidden',!hasResults);
  const errors=[],seen=new Set();
  for(const item of [...state.errors||[],...(technical.summary||{}).errors||[],...(ai.summary||{}).errors||[]]){
    const key=JSON.stringify(item);if(!seen.has(key)){seen.add(key);errors.push(item)}
  }
  $('file-errors').replaceChildren();
  if(errors.length){
    const heading=document.createElement('h3'),help=document.createElement('p'),list=document.createElement('ul');
    heading.textContent='Files that need attention';help.className='help';list.className='errors';
    help.textContent='Repair unreadable files or install the missing tool, then retry. Successful files are reused from cache.';
    for(const item of errors){
      const li=document.createElement('li'),path=document.createElement('code'),message=document.createElement('div');
      path.textContent=item.path||item.file||'Analysis';message.textContent=item.error||item.message||lines(item);
      li.append(path,message);
      const recovery=(state.recovery||[]).find(hint=>hint.path===item.path);
      if(recovery&&recovery.action){const hint=document.createElement('p');hint.className='help';hint.textContent=recovery.action;li.append(hint)}
      list.append(li);
    }
    $('file-errors').append(heading,help,list);
  }
  const total=Object.values(state.counts||{}).reduce((sum,count)=>sum+(Number(count)||0),0);
  let message=state.error||technical.error||ai.error||'';
  if(!message){
    if(state.busy)message='Work is still running. Results below are saved observations, not a completion guarantee.';
    else if(total===0)message='No supported media found. Add supported photos, video or audio to the source folder, then recheck readiness and analyze again.';
    else if(['partial','failed','interrupted','cancelled'].includes(state.status))message='Some work is incomplete. Review the errors and readiness guidance, then retry. Completed work is preserved.';
    else message='Saved results are available now. Add local AI at any time without starting the technical catalog over.';
  }
  notice('result-notice',message);
  activeStep(state.busy?'analysis':hasResults?'results':'readiness');
  flags();
  if(state.busy)schedulePoll();else if(hasResults)loadHandoff();
}
function schedulePoll(){clearTimeout(timer);timer=setTimeout(async()=>{try{render(await api('/api/state'));if(pending&&!state.busy)schedulePoll()}catch(error){showError(error);schedulePoll()}},1000)}
async function loadHandoff(){try{const value=await api('/api/handoff?inspect='+($('inspect').checked?'true':'false'));$('prompt').textContent=value.prompt||'';$('mcp').textContent=JSON.stringify(value.config||{},null,2);$('handoff-instructions').textContent=lines(value.instructions||'')}catch(error){$('handoff-instructions').textContent=error.message}}
function showReadiness(value){
  $('readiness').classList.remove('hidden');activeStep('readiness');
  $('ai-setup').hidden=!(state&&state.project);
  document.querySelector('a[href="#analysis"].button').hidden=!(state&&state.project);
  countPills('media-counts',Object.entries(value.counts||{}));
  notice('readiness-notice',[...(value.errors||[]),...(value.warnings||[])].join('\n')||'Media and tools are ready. Open or create the project to continue.');
  $('readiness-notice').classList.toggle('warn',!!(value.errors||[]).length);
  $('readiness-fixes').replaceChildren();
  const pre=document.createElement('pre');pre.textContent=lines(value.installation_instructions||[]);
  $('readiness-fixes').append(pre);$('readiness').scrollIntoView({block:'start'});
}
$('project-form').onsubmit=event=>{event.preventDefault();action('Opening your local project…',async()=>{
  const source=$('source').value.trim(),project=$('project').value.trim();
  try{const result=await api('/api/start',{source,project});applySettings(result.project.settings||{});render(result);$('open-status').textContent='Project open. Saved results restored.';$('readiness').scrollIntoView({block:'start'})}
  catch(error){if(source)showReadiness(await api('/api/readiness',{source}));throw error}
})};
$('refresh-readiness').onclick=()=>action('Checking tools and media…',async()=>{if(state&&state.project)render(await api('/api/start',{project:state.project.directory,source:state.project.source}));else showReadiness(await api('/api/readiness',{source:$('source').value.trim()}))});
$('save-limits').onclick=()=>action('Saving timeout limits…',async()=>{const values={};for(const name of numberFields){values[name]=Number($(name).value);if(!Number.isFinite(values[name])||values[name]<=0)throw Error('Timeouts must be positive numbers.')}render(await api('/api/configure',{settings:values}));notice('ai-feedback','Timeout limits saved. No AI capabilities were enabled and no analysis was started.');});
async function analyze(operation){await action(operation==='ai'?'Starting local AI enrichment…':'Starting technical analysis…',async()=>{render(await api('/api/analyze',{operation}));schedulePoll()})}
$('analyze').onclick=()=>analyze('technical');$('enrich').onclick=()=>analyze('ai');$('retry').onclick=()=>analyze(state.operation==='ai'?'ai':'technical');$('cancel').onclick=()=>action('Stopping after current work…',async()=>{render(await api('/api/cancel',{}));schedulePoll()});
$('add-ai').onclick=()=>{$('ai-setup').open=true;$('ai-setup').scrollIntoView({block:'start'});$('detect-ai').focus()};
$('diagnostics').onclick=()=>action('Saving diagnostic report…',async()=>{const value=await api('/api/diagnostics',{});$('diagnostic-result').textContent='Report saved locally: '+value.path;let details=$('diagnostic-report');if(!details){details=document.createElement('details');details.id='diagnostic-report';const heading=document.createElement('summary');heading.textContent='View diagnostic report';const pre=document.createElement('pre');pre.id='diagnostic-text';details.append(heading,pre);$('diagnostic-result').after(details)}$('diagnostic-text').textContent=lines(value.report);});
$('open-folder').onclick=()=>action('Opening the project folder…',async()=>{await api('/api/open-folder',{});$('diagnostic-result').textContent='Requested the local file manager. Project: '+state.project.directory});
$('inspect').onchange=loadHandoff;$('copy').onclick=async()=>{try{await navigator.clipboard.writeText($('prompt').textContent);$('copied').textContent='Copied. Paste this into your existing agent.'}catch{$('copied').textContent='Clipboard unavailable. Select the prompt above and copy it manually.'}};
async function browse(path){$('browse-error').textContent='';try{const value=await api('/api/browse?path='+encodeURIComponent(path||''));$('browse-path').value=value.path;folderParent=value.parent;$('browse-up').disabled=!folderParent||folderParent===value.path;$('directories').replaceChildren();for(const directory of value.directories||[]){const item=document.createElement('li'),button=document.createElement('button');button.type='button';button.textContent=directory.name;button.onclick=()=>browse(directory.path);item.append(button);$('directories').append(item)}$('browse-note').textContent=value.truncated?'This folder has many directories; type a path to reach one not listed.':(value.directories||[]).length?'Select a folder to open it, or use the current folder.':'No subfolders. You can use the current folder.'}catch(error){$('browse-error').textContent=error.message}}
async function suggestProject(source){if(!source)return;try{const value=await api('/api/readiness',{source});if($('source').value.trim()!==source)return;if(!$('project').value.trim()&&value.suggested_project){$('project').value=value.suggested_project;$('open-status').textContent='Suggested project folder — editable. StoryLens creates it; existing folders are never overwritten.'}if(!(state&&state.project))showReadiness(value)}catch(error){showError(error)}}
$('source').onchange=()=>suggestProject($('source').value.trim());
document.querySelectorAll('.browse').forEach(button=>button.onclick=()=>{folderTarget=button.dataset.target;$('folder-dialog').showModal();browse($(folderTarget).value.trim()||'')});$('browse-form').onsubmit=event=>{event.preventDefault();browse($('browse-path').value)};$('browse-up').onclick=()=>browse(folderParent);$('browse-close').onclick=()=>$('folder-dialog').close();$('browse-select').onclick=()=>{$(folderTarget).value=$('browse-path').value;$('folder-dialog').close();$(folderTarget).focus();if(folderTarget==='source')suggestProject($('source').value.trim())};
function invalidateAI(){checkPassed=false;samplePassed=false;flags()}
for(const id of [...textFields,...numberFields,'enable-vision','enable-embedding','enable-transcription'])$(id).addEventListener('input',invalidateAI);
function settings(requireCapability=true){const result={};for(const name of textFields)result[name]=$(name).value.trim();for(const name of numberFields){result[name]=Number($(name).value);if(!Number.isFinite(result[name])||result[name]<=0)throw Error('Timeouts must be positive numbers.')}if(!$('enable-vision').checked)result.vision_model='';if(!$('enable-embedding').checked)result.embedding_model='';if(!$('enable-transcription').checked)result.audio_transcription_model='';if(requireCapability&&!result.vision_model&&!result.embedding_model&&!result.audio_transcription_model)throw Error('Choose at least one AI capability and provide its installed model name or folder. Or choose technical analysis only.');return result}
function aiFeedback(value){const messages=[value.ready?'Setup is ready.':'Setup needs attention.',...value.errors||[],...value.warnings||[]];for(const result of value.results||[]){const title=({embeddings:'Visual search',vision:'Image descriptions',transcription:'Speech transcription'})[result.capability]||'Local AI';messages.push(title+': sample completed.');if(result.source)messages.push('Sample: '+result.source);if(result.caption)messages.push('AI description (may be incorrect): '+result.caption);if(result.capability==='transcription')messages.push(result.text?'Recognized speech: '+result.text:'No spoken words were recognized in this short sample.');if(result.coverage)messages.push(result.coverage)}notice('ai-feedback',messages.join('\n'))}
function recommendation(name){const id=name==='embedding'?'embeddings':name;return (options&&options.capabilities||[]).find(item=>item.id===id)||{}}
function modelSize(model){return model.download_bytes?'About '+(model.download_bytes/1000000000).toFixed(2)+' GB download':''}
function renderOptions(value){options=value;$('ai-detected').textContent='Detected local capabilities below. Installed models can be selected without downloading again. A saved profile is never enabled automatically.';const commands=[...(value.instructions||[]),...(value.capabilities||[]).filter(item=>!item.installed&&item.install_command).map(item=>item.title+': '+item.install_command)];$('ai-dependencies').replaceChildren();if(commands.length){const heading=document.createElement('h3'),pre=document.createElement('pre');heading.textContent='Optional dependencies · run only what you need';pre.textContent=lines(commands);$('ai-dependencies').append(heading,pre)}for(const name of ['vision','embedding','transcription']){const model=recommendation(name);$('recommend-'+name).textContent=[model.recommendation||'',model.installed?'Installed locally':modelSize(model),model.hardware||''].filter(Boolean).join(' · ');const field={vision:'vision_model',embedding:'embedding_model',transcription:'audio_transcription_model'}[name];if(model.installed&&model.model_path&&!$(field).value)$(field).value=model.model_path}}
$('detect-ai').onclick=()=>action('Detecting installed local tools and models…',async()=>renderOptions(await api('/api/ai/options')));
$('check-ai').onclick=()=>action('Checking local dependencies and model paths…',async()=>{const result=await api('/api/ai/check',{settings:settings()});checkPassed=!!result.ready;samplePassed=false;aiFeedback(result)});
$('sample-ai').onclick=()=>action('Testing local AI on a small media sample. This can take a few minutes…',async()=>{const result=await api('/api/ai/sample',{settings:settings()});samplePassed=!!result.ready;aiFeedback(result);if(result.ready)notice('ai-feedback',$('ai-feedback').textContent+'\nSample succeeded. Save this setup, then choose Run local AI enrichment.')});
$('save-ai').onclick=()=>action('Saving AI setup for this project…',async()=>{render(await api('/api/configure',{settings:settings()}));notice('ai-feedback','AI setup saved. Choose Run local AI enrichment to add evidence to this catalog. Saving does not start analysis.');$('enrich').focus()});
$('technical-only').onclick=()=>action('Keeping this project technical-only…',async()=>{render(await api('/api/configure',{settings:{mode:'technical'}}));notice('ai-feedback','Technical-only mode saved. You can enable local AI later.');});
$('load-profile').onclick=()=>action('Loading your reusable local AI profile…',async()=>{const value=await api('/api/ai/profile',{action:'load'});applySettings(value.settings||{});notice('ai-feedback','Saved profile loaded into this form, not enabled yet. Check setup and test a sample before saving it to this project.');});
$('save-profile').onclick=()=>action('Saving a reusable local AI profile…',async()=>{await api('/api/ai/profile',{action:'save',settings:settings()});notice('ai-feedback','Reusable profile saved locally. Other projects must explicitly load it; this does not start analysis.');});
document.querySelectorAll('.download').forEach(button=>button.onclick=()=>action('Loading recommended model details…',async()=>{if(!options)renderOptions(await api('/api/ai/options'));capability=button.dataset.capability==='embedding'?'embeddings':button.dataset.capability;const model=recommendation(capability);$('download-description').textContent=[model.recommendation||'Recommended '+capability+' model',model.description||'',modelSize(model)||'Size varies by model; allow several GB of free disk space.',model.hardware||''].filter(Boolean).join('\n');$('download-consent').checked=false;$('download-dialog').showModal();}));
$('download-consent').onchange=flags;$('download-close').onclick=()=>$('download-dialog').close();$('download-confirm').onclick=()=>{if(!$('download-consent').checked)return;$('download-dialog').close();action('Downloading the '+capability+' model. This may take several minutes; keep this page and terminal open…',async()=>{const value=await api('/api/ai/install',{capability,consent:true,settings:settings(false)});const current={...Object.fromEntries([...textFields,...numberFields].map(name=>[name,$(name).value])),...(value.settings||{})};applySettings(current);notice('ai-feedback','Model download finished. Review the selected capabilities, check setup, then test a sample. No whole-folder AI analysis has started.');});};
async function restore(){if(!token){showError(Error('This page needs the private session link printed in the StoryLens terminal (including the part after #).'));return}await action('Restoring your local session…',async()=>{let snapshot=await api('/api/state');if(!snapshot.project&&params.get('project'))snapshot=await api('/api/start',{project:params.get('project'),source:params.get('source')||''});if(snapshot.project)applySettings(snapshot.project.settings||{});render(snapshot);if(!snapshot.project&&params.get('source')&&!params.get('project'))await suggestProject(params.get('source'));});}
restore();
</script></main></body></html>'''


def _handler(session, token):
    """Bind HTTP transport only; project state and work belong to WizardSession."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "StoryLensLocal/2"

        def log_message(self, format, *args):
            pass

        def _send(self, raw, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, value, status=200):
            self._send(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", status)

        def _authorized(self, *, require_origin=False):
            host = f"127.0.0.1:{self.server.server_port}"
            return (self.headers.get("Host") == host
                    and (not require_origin or self.headers.get("Origin") == f"http://{host}")
                    and secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token))

        def _error(self, error):
            status = 400 if isinstance(error, (OSError, ValueError, TypeError, RuntimeError, KeyError)) else 500
            return self._json({"error": str(error) or type(error).__name__}, status)

        def do_GET(self):
            parsed = urlsplit(self.path)
            route = parsed.path
            if route == "/":
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    return self._json({"error": "Invalid host"}, 403)
                return self._send(_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if not self._authorized():
                return self._json({"error": "Not found or unauthorized"}, 404)
            query = parse_qs(parsed.query)
            try:
                if route == "/api/state":
                    return self._json(session.snapshot())
                if route == "/api/browse":
                    return self._json(session.browse(query.get("path", [""])[0]))
                if route == "/api/ai/options":
                    return self._json(session.ai_options())
                if route == "/api/handoff":
                    return self._json(session.handoff(inspect=query.get("inspect", ["false"])[0] == "true"))
                return self._json({"error": "Not found"}, 404)
            except Exception as error:
                return self._error(error)

        def do_POST(self):
            if not self._authorized(require_origin=True):
                return self._json({"error": "Not found or unauthorized"}, 404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 32768:
                    raise ValueError("Request is too large")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Request must be an object")
                route = urlsplit(self.path).path
                if route == "/api/readiness":
                    source = body.get("source", "")
                    if not isinstance(source, str):
                        raise ValueError("The media folder path must be text")
                    return self._json(session.readiness(source))
                if route == "/api/start":
                    project, source = body.get("project", ""), body.get("source", "")
                    if not isinstance(project, str) or not isinstance(source, str):
                        raise ValueError("Source and project paths must be text")
                    return self._json(session.open(project=project.strip(), source=source.strip()))
                if route == "/api/analyze":
                    session.analyze(operation=body.get("operation", "technical"))
                    return self._json(session.snapshot(), 202)
                if route == "/api/cancel":
                    session.cancel()
                    return self._json(session.snapshot())
                if route == "/api/state":
                    return self._json(session.snapshot())
                if route in ("/api/configure", "/api/ai/check", "/api/ai/sample"):
                    settings = body.get("settings", {})
                    if not isinstance(settings, dict):
                        raise ValueError("Settings must be an object")
                    method = {"/api/configure": session.configure, "/api/ai/check": session.check_ai,
                              "/api/ai/sample": session.sample_ai}[route]
                    return self._json(method(settings))
                if route == "/api/ai/install":
                    consent = body.get("consent", False)
                    if not isinstance(consent, bool):
                        raise ValueError("Download consent must be true or false")
                    settings = body.get("settings")
                    if settings is not None and not isinstance(settings, dict):
                        raise ValueError("Settings must be an object")
                    return self._json(session.install_model(
                        capability=body.get("capability", ""), consent=consent, settings=settings))
                if route == "/api/ai/profile":
                    settings = body.get("settings")
                    if settings is not None and not isinstance(settings, dict):
                        raise ValueError("Settings must be an object")
                    return self._json(session.ai_profile(action=body.get("action", "load"), settings=settings))
                if route == "/api/diagnostics":
                    return self._json(session.diagnostics())
                if route == "/api/open-folder":
                    result = session.open_folder()
                    return self._json(result if isinstance(result, dict) else {"opened": True})
                return self._json({"error": "Unknown endpoint"}, 404)
            except Exception as error:
                return self._error(error)

    return Handler


def run(config: Config, *, project=None, source=None):
    """Serve a private wizard on IPv4 loopback, opening only the local browser."""
    token = secrets.token_urlsafe(32)
    session = WizardSession(config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(session, token))
    server.daemon_threads = True
    parameters = {key: value for key, value in (("project", project), ("source", source)) if value}
    query = "?" + urlencode(parameters) if parameters else ""
    url = f"http://127.0.0.1:{server.server_port}/" + query + "#" + token
    print("StoryLens is running locally. Keep this terminal open; Ctrl+C stops the browser server.", flush=True)
    print("Open: " + url, flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        webbrowser.open(url)
        threading.Event().wait()
    except KeyboardInterrupt:
        try:
            session.cancel()
        except ValueError:
            pass  # An idle or just-completed session needs no cancellation.
    finally:
        server.shutdown()
        server.server_close()
