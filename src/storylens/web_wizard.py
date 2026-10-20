"""Loopback-only browser workflow for private media analysis and MCP handoff."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import threading
import webbrowser
from urllib.parse import urlencode

from .config import Config
from .workflow import Project, preflight

_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StoryLens · Your media, understood</title>
<style>
:root{color-scheme:light;--ink:#17231f;--muted:#62736b;--paper:#f5f4ed;--card:#fffefa;--green:#2b694c;--lime:#d5e6a9;--line:#d9ded4;--red:#a33c31}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}.wrap{max-width:880px;margin:0 auto;padding:42px 24px 80px}.brand{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--green);font-weight:800}.hero{margin:28px 0 34px}.hero h1{font-family:Georgia,serif;font-weight:500;font-size:clamp(36px,6vw,60px);line-height:1.04;letter-spacing:-.04em;margin:0 0 14px;max-width:680px}.hero p{color:var(--muted);max-width:610px;margin:0;font-size:18px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:26px;margin:18px 0;box-shadow:0 8px 26px #2335280a}.step{display:flex;gap:14px;align-items:flex-start}.num{width:31px;height:31px;flex:0 0 31px;border-radius:50%;display:grid;place-items:center;background:var(--lime);color:var(--green);font-weight:800}.card h2{font-size:20px;margin:0 0 6px}.sub{color:var(--muted);margin:0 0 20px}.fields{display:grid;grid-template-columns:1fr 1fr;gap:14px}.field{min-width:0}.field.full{grid-column:1/-1}label{display:block;font-size:13px;font-weight:700;margin:0 0 6px}input[type=text]{width:100%;border:1px solid var(--line);border-radius:9px;background:white;padding:12px;font:inherit;color:var(--ink)}button{border:0;border-radius:9px;background:var(--green);color:white;font:inherit;font-weight:750;padding:12px 18px;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.secondary{background:#e8eee4;color:var(--green)}.actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:18px}.notice{font-size:14px;color:var(--muted);padding:12px 14px;border-radius:10px;background:#f0f2eb}.status{font-weight:700}.error{color:var(--red);white-space:pre-wrap}.counts{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}.pill{border:1px solid var(--line);border-radius:99px;padding:5px 11px;background:white;font-size:14px}.meter{height:9px;background:#e7eae2;border-radius:99px;overflow:hidden;margin:15px 0}.meter i{display:block;height:100%;width:0;background:var(--green);transition:width .25s}.prompt{white-space:pre-wrap;background:#f1f2ec;border:1px solid var(--line);border-radius:10px;padding:16px;font:13px/1.5 ui-monospace,monospace;max-height:330px;overflow:auto}.consent{display:flex;gap:10px;align-items:flex-start;margin:16px 0}.consent input{margin-top:5px;accent-color:var(--green)}.hidden{display:none}.footer{font-size:13px;color:var(--muted);margin-top:28px}@media(max-width:600px){.wrap{padding:28px 16px 60px}.fields{grid-template-columns:1fr}.field.full{grid-column:auto}.card{padding:20px}}
</style></head><body><main class="wrap"><div class="brand">StoryLens · private by design</div><header class="hero"><h1>Your memories,<br>ready for the next cut.</h1><p>Analyze a local folder, then give your AI agent the evidence it needs to build an editable home video in FilmCraft.</p></header>
<section class="card"><div class="step"><span class="num">1</span><div><h2>Choose your media and project folder</h2><p class="sub">The source is read-only. Analysis data stays in a separate local project folder.</p><div class="fields"><div class="field full"><label for="source">Media folder</label><input id="source" type="text" placeholder="/path/to/photos-and-videos"></div><div class="field full"><label for="project">StoryLens project folder</label><input id="project" type="text" placeholder="/path/to/private-project"></div></div><div class="actions"><button id="start">Check folder</button><span id="status" class="status" aria-live="polite"></span></div><div id="preflight" class="notice hidden"></div><div id="error" class="error" role="alert"></div></div></div></section>
<section id="analysis" class="card hidden"><div class="step"><span class="num">2</span><div style="flex:1"><h2>Analyze locally</h2><p class="sub">StoryLens reads metadata and local technical signals. It does not send media to an AI service.</p><div class="counts" id="media-counts"></div><div class="meter"><i id="bar"></i></div><div id="progress" class="status" aria-live="polite"></div><div id="analysis-error" class="error"></div><div class="actions"><button id="analyze">Start analysis</button></div></div></div></section>
<section id="handoff" class="card hidden"><div class="step"><span class="num">3</span><div style="flex:1"><h2>Hand off to your AI agent</h2><p class="sub">StoryLens supplies evidence. Your existing agent makes creative choices and uses its configured FilmCraft MCP tools.</p><div class="notice" id="mcp"></div><label class="consent"><input type="checkbox" id="inspect"><span><strong>Allow my agent to inspect the original media</strong><br>Required for subjective clip selection and visual continuity. This prompt does not launch the agent or transfer files. If your agent uses a cloud provider, approve media sharing with that provider separately.</span></label><div class="prompt" id="prompt"></div><div class="actions"><button id="copy">Copy handoff prompt</button><span id="copied" class="status" aria-live="polite"></span></div></div></div></section>
<p class="footer">This page is served only on this computer. StoryLens does not connect to your AI agent or FilmCraft; you control their MCP configuration and every export.</p></main>
<script>
const $=id=>document.getElementById(id);let token=location.hash.slice(1),projectState=null,counts=null;
const params=new URLSearchParams(location.search);$('source').value=params.get('source')||'';$('project').value=params.get('project')||'';
$('start').textContent='Open or create project';
async function api(path,data){let r=await fetch(path,{method:data?'POST':'GET',headers:{'Authorization':'Bearer '+token,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined});let j=await r.json();if(!r.ok)throw Error(j.error||'Request failed');return j}
function err(e){$('error').textContent=e.message}
$('start').onclick=async()=>{err({message:''});$('status').textContent='Checking…';$('start').disabled=true;try{let s=await api('/api/start',{source:$('source').value.trim(),project:$('project').value.trim()});projectState=s.project;counts=s.counts;$('preflight').classList.remove('hidden');$('preflight').innerHTML=s.warnings.map(x=>'<div>'+escapeHtml(x)+'</div>').join('')||'Folder is ready.';$('media-counts').innerHTML=Object.entries(counts).map(([k,v])=>`<span class="pill">${escapeHtml(k)} · ${v}</span>`).join('');$('analysis').classList.remove('hidden');$('status').textContent='Ready';$('analyze').textContent=s.existing?'Resume analysis':'Start analysis';$('analysis-error').textContent='';}catch(e){err(e);$('status').textContent=''}finally{$('start').disabled=false}};
$('analyze').onclick=async()=>{ $('analyze').disabled=true;$('analysis-error').textContent='';try{await api('/api/analyze',{});$('progress').textContent='Starting…';poll()}catch(e){$('analysis-error').textContent=e.message;$('analyze').disabled=false}};
async function poll(){try{let s=await api('/api/state');if(s.summary){let all=s.summary.discovered||0,finished=s.summary.indexed||0;$('bar').style.width=(all?Math.min(100,finished/all*100):0)+'%';$('progress').textContent=`${s.summary.indexed||0}/${all} indexed · ${s.summary.analyzed||0} analyzed · ${s.summary.cache_hits||0} cached · ${s.summary.failed||0} failed`;}if(s.status==='running'){setTimeout(poll,1000);return}if(s.status==='completed'||s.status==='partial'){$('analyze').disabled=false;$('analyze').textContent='Analyze again (reuse cache)';$('handoff').classList.remove('hidden');$('progress').textContent+=' · '+(s.status==='partial'?'Analysis has failures; inspect the error list before relying on this handoff.':'Analysis complete.');$('analysis-error').textContent=(s.errors||[]).map(x=>`${x.path||''} [${x.stage||''}]: ${x.error||''}`).join('\\n');updateHandoff();return}$('analysis-error').textContent=s.error||'Analysis stopped.';$('analyze').disabled=false}catch(e){$('analysis-error').textContent=e.message;$('analyze').disabled=false}}
function updateHandoff(){if(!projectState)return;let bin=projectState.executable||'storylens';let cfg={mcpServers:{storylens:{command:bin,args:['--database',projectState.database,'serve','--allow-root',projectState.source]}}};$('mcp').textContent=`The StoryLens MCP server requires the optional MCP SDK in the same Python environment as this executable. Install it with: python -m pip install 'storylens[mcp]'.\n\nAdd this server to your MCP host configuration:\n${JSON.stringify(cfg,null,2)}\n\nThis config authorizes that host to use StoryLens MCP against the approved source root. The inspection checkbox changes only the copied prompt; it is not a filesystem access control. Configure this only in a host you trust.`;$('mcp').style.whiteSpace='pre-wrap';$('mcp').style.fontFamily='ui-monospace,monospace';let inspect=$('inspect').checked;let text=`Use StoryLens MCP to review the analyzed collection in ${projectState.database}. ${inspect?'I explicitly allow you to inspect the original media in '+projectState.source+'.':'Do not open, decode, or inspect original media until I explicitly authorize it.'} Ask me about desired story, length, tone, and music if I have not specified them. Do not transmit media, frames, transcripts, paths, or private analysis to a cloud provider without my separate explicit approval. Use only the FilmCraft MCP tools advertised by my configured server; do not assume undocumented tool names or claim unsupported features. Select strong, story-relevant clips; use sensible source in/out points, handle still photos with deliberate holds and motion where supported, and add appropriate transitions and music where supported. Before creating the native project, show me the planned clips, transitions, image treatment, and music and get my approval. Then create a NEW editable native FilmCraft project; preserve the original media and StoryLens project. Report the project path, choices, warnings, and any unsupported requests. Do not export a final movie unless I separately approve.`;$('prompt').textContent=text}
$('inspect').onchange=updateHandoff;$('copy').onclick=async()=>{try{await navigator.clipboard.writeText($('prompt').textContent);$('copied').textContent='Copied.'}catch{$('copied').textContent='Clipboard unavailable; select and copy the prompt.'}};
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
</script></body></html>'''


class _State:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.project = None
        self.status = "idle"
        self.summary = None
        self.error = None

    def snapshot(self):
        with self.lock:
            summary = self.summary or {}
            return {"status": self.status, "summary": summary,
                    "errors": summary.get("errors", []), "error": self.error}


def _handler(state, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "StoryLensLocal/1"

        def log_message(self, format, *args):
            pass

        def _json(self, value, status=200):
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self, *, require_origin=False):
            expected_host = f"127.0.0.1:{self.server.server_port}"
            origin = f"http://{expected_host}"
            return (self.headers.get("Host") == expected_host
                    and (not require_origin or self.headers.get("Origin") == origin)
                    and secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token))

        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if route == "/api/state":
                if not self._authorized():
                    return self._json({"error": "Not found or unauthorized"}, 404)
                return self._json(state.snapshot())
            if route != "/":
                return self._json({"error": "Not found"}, 404)
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                return self._json({"error": "Invalid host"}, 403)
            raw = _PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(raw)

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
                if self.path == "/api/start":
                    if state.status == "running":
                        raise ValueError("Analysis is already running")
                    raw_source, raw_directory = body.get("source", ""), body.get("project", "")
                    if not isinstance(raw_source, str) or not isinstance(raw_directory, str):
                        raise ValueError("Source and project paths must be text")
                    source, directory = raw_source.strip(), raw_directory.strip()
                    if not directory:
                        raise ValueError("Choose a project folder")
                    path = Path(directory).expanduser()
                    if path.exists():
                        project = Project.open(path)
                        if source and Path(source).expanduser().resolve() != project.source:
                            raise ValueError("Source differs from the saved project source")
                        checked = preflight(project.source, project.config, mode=project.mode, depth=project.depth)
                    else:
                        if not source:
                            raise ValueError("Choose an existing media folder")
                        checked = preflight(Path(source).expanduser(), state.config)
                        if checked["errors"]:
                            raise ValueError("Preflight failed: " + "; ".join(checked["errors"]))
                        project = Project.create(path, source, state.config)
                    if checked["errors"]:
                        raise ValueError("Preflight failed: " + "; ".join(checked["errors"]))
                    state.project = project
                    with state.lock:
                        state.status, state.summary, state.error = "ready", None, None
                    return self._json({"project": {"source": str(project.source),
                        "directory": str(project.directory), "database": str(project.config.database),
                        "executable": shutil.which("storylens") or "storylens"},
                        "counts": checked["counts"], "warnings": checked["warnings"],
                        "existing": project.state["analysis"]["status"] != "not_started"})
                if self.path == "/api/analyze":
                    if state.project is None or state.status == "running":
                        raise ValueError("Choose a project first, or wait for the current analysis")
                    with state.lock:
                        state.status, state.summary, state.error = "running", None, None

                    def work():
                        def update(summary):
                            with state.lock:
                                state.summary = summary
                        try:
                            summary = state.project.analyze(progress=update)
                            with state.lock:
                                state.summary = summary
                                state.status = "partial" if summary.get("failed") else "completed"
                        except BaseException as error:
                            with state.lock:
                                state.error = str(error)[:2000]
                                state.status = "failed"
                    threading.Thread(target=work, daemon=True).start()
                    return self._json({"status": "running"}, 202)
                if self.path == "/api/state":
                    return self._json(state.snapshot())
                return self._json({"error": "Unknown endpoint"}, 404)
            except (OSError, ValueError, TypeError, RuntimeError, KeyError) as error:
                return self._json({"error": str(error)}, 400)
            except Exception as error:
                return self._json({"error": str(error) or type(error).__name__}, 500)

    return Handler


def run(config: Config, *, project=None, source=None):
    """Serve a token-protected wizard on loopback and open it in the browser."""
    token = secrets.token_urlsafe(32)
    state = _State(config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state, token))
    base = f"http://127.0.0.1:{server.server_port}/"
    server.daemon_threads = True
    parameters = {key: value for key, value in (("project", project), ("source", source)) if value}
    query = "?" + urlencode(parameters) if parameters else ""
    url = base + query + "#" + token
    print("StoryLens is running locally. Close this terminal to stop it.", flush=True)
    print("Open: " + url, flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        webbrowser.open(url)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
