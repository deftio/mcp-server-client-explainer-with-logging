from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from flask import Flask, Response, render_template_string, request

LOG_DIR = "./logs"
POLL_INTERVAL_SEC = 0.5

app = Flask(__name__)

INDEX_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MCP Logs</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
      body { padding-top: 1rem; }
      pre { white-space: pre-wrap; }
      .log-line { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12px; }
      .sticky-top-2 { position: sticky; top: 0; z-index: 1000; background: white; padding-top: .5rem; padding-bottom: .5rem; }
    </style>
  </head>
  <body>
    <div class="container-fluid">
      <div class="row sticky-top-2">
        <div class="col-md-8 d-flex align-items-end gap-2">
          <div>
            <label class="form-label">Files</label>
            <select id="files" class="form-select" multiple size="3"></select>
          </div>
          <div class="flex-grow-1">
            <label class="form-label">Filter (JSONPath-ish: level=INFO, component=mcp-server, event=tool_call_success)</label>
            <input id="filter" type="text" class="form-control" placeholder="level=ERROR,component=mcp-server">
          </div>
          <div>
            <label class="form-label">Follow</label>
            <div class="form-check form-switch">
              <input class="form-check-input" type="checkbox" role="switch" id="follow" checked>
              <label class="form-check-label" for="follow">Auto-scroll</label>
            </div>
          </div>
          <div class="align-self-end">
            <a href="/dashboard" class="btn btn-outline-secondary">Dashboard</a>
            <button id="apply" class="btn btn-primary">Apply</button>
          </div>
        </div>
        <div class="col-md-4">
          <div class="card">
            <div class="card-body">
              <div><strong>Tips</strong></div>
              <div>- Multiple files: Cmd/Ctrl-click to select more.</div>
              <div>- Filter is key=value pairs separated by commas.</div>
              <div>- Examples: level=ERROR, component=mcp-client-chat</div>
            </div>
          </div>
        </div>
      </div>
      <div class="row mt-2">
        <div class="col-12">
          <div id="log" class="border rounded p-2" style="height: 70vh; overflow: auto;"></div>
        </div>
      </div>
    </div>

    <script>
      let es;
      function fetchFiles() {
        fetch('/files').then(r => r.json()).then(files => {
          const s = document.getElementById('files');
          s.innerHTML = '';
          files.forEach(f => {
            const opt = document.createElement('option');
            opt.value = f;
            opt.textContent = f;
            s.appendChild(opt);
          });
        });
      }

      function parseFilter(txt) {
        const obj = {};
        txt.split(',').map(s => s.trim()).filter(Boolean).forEach(pair => {
          const idx = pair.indexOf('=');
          if (idx > -1) {
            obj[pair.slice(0, idx).trim()] = pair.slice(idx+1).trim();
          }
        });
        return obj;
      }

      function startStream() {
        if (es) es.close();
        const selected = Array.from(document.getElementById('files').selectedOptions).map(o => o.value);
        const filterObj = parseFilter(document.getElementById('filter').value);
        const params = new URLSearchParams();
        if (selected.length) params.set('files', selected.join(','));
        if (Object.keys(filterObj).length) params.set('filter', JSON.stringify(filterObj));
        es = new EventSource('/stream?' + params.toString());
        const log = document.getElementById('log');
        log.innerHTML = '';
        es.onmessage = (ev) => {
          const div = document.createElement('div');
          div.className = 'log-line';
          div.textContent = ev.data;
          log.appendChild(div);
          if (document.getElementById('follow').checked) {
            log.scrollTop = log.scrollHeight;
          }
        };
      }

      document.getElementById('apply').addEventListener('click', startStream);
      window.addEventListener('load', () => {
        fetchFiles();
        setTimeout(startStream, 300);
      });
    </script>
  </body>
</html>
"""

DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MCP Logs Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
      body { padding-top: 1rem; }
      .log-line { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12px; }
      .panel { height: 28vh; overflow: auto; border: 1px solid #e5e5e5; border-radius: .5rem; padding: .5rem; background: #fff; }
      .controls { position: sticky; top: 0; z-index: 1000; background: white; padding: .25rem 0; }
    </style>
  </head>
  <body>
    <div class="container-fluid">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <h5 class="m-0">MCP Logs Dashboard</h5>
        <div class="d-flex gap-2">
          <a href="/" class="btn btn-outline-secondary btn-sm">Advanced View</a>
          <button id="reload" class="btn btn-primary btn-sm">Reload Streams</button>
        </div>
      </div>

      <div class="row g-3">
        <div class="col-12">
          <div class="card">
            <div class="card-header">All Logs (merged)</div>
            <div class="card-body">
              <div class="controls d-flex gap-2 align-items-end">
                <div class="flex-grow-1">
                  <label class="form-label">Filter</label>
                  <input id="flt-all" type="text" class="form-control" placeholder="level=ERROR">
                </div>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="follow-all" checked>
                  <label class="form-check-label" for="follow-all">Follow</label>
                </div>
              </div>
              <div id="panel-all" class="panel"></div>
            </div>
          </div>
        </div>

        <div class="col-md-6">
          <div class="card">
            <div class="card-header">Server Logs (mcp-server)</div>
            <div class="card-body">
              <div class="controls d-flex gap-2 align-items-end">
                <div class="flex-grow-1">
                  <label class="form-label">Filter</label>
                  <input id="flt-server" type="text" class="form-control" placeholder="component=mcp-server">
                </div>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="follow-server" checked>
                  <label class="form-check-label" for="follow-server">Follow</label>
                </div>
              </div>
              <div id="panel-server" class="panel"></div>
            </div>
          </div>
        </div>

        <div class="col-md-6">
          <div class="card">
            <div class="card-header">Client Logs (simple + chat)</div>
            <div class="card-body">
              <div class="controls d-flex gap-2 align-items-end">
                <div class="flex-grow-1">
                  <label class="form-label">Filter</label>
                  <input id="flt-clients" type="text" class="form-control" placeholder="component=mcp-client-chat">
                </div>
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="follow-clients" checked>
                  <label class="form-check-label" for="follow-clients">Follow</label>
                </div>
              </div>
              <div id="panel-clients" class="panel"></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <script>
      let esAll, esServer, esClients;

      function parseFilter(txt) {
        const obj = {};
        txt.split(',').map(s => s.trim()).filter(Boolean).forEach(pair => {
          const idx = pair.indexOf('=');
          if (idx > -1) obj[pair.slice(0, idx).trim()] = pair.slice(idx+1).trim();
        });
        return obj;
      }

      async function getFiles() {
        const res = await fetch('/files');
        return await res.json();
      }

      function openStream(targetEl, followEl, files, filterTxt) {
        const params = new URLSearchParams();
        if (files && files.length) params.set('files', files.join(','));
        const flt = parseFilter(filterTxt || '');
        if (Object.keys(flt).length) params.set('filter', JSON.stringify(flt));
        const es = new EventSource('/stream?' + params.toString());
        targetEl.innerHTML = '';
        es.onmessage = (ev) => {
          const div = document.createElement('div');
          div.className = 'log-line';
          div.textContent = ev.data;
          targetEl.appendChild(div);
          if (followEl.checked) targetEl.scrollTop = targetEl.scrollHeight;
        };
        return es;
      }

      async function reload() {
        const files = await getFiles();
        const serverFiles = files.filter(f => f.includes('mcp-server'));
        const clientFiles = files.filter(f => f.includes('mcp-client'));

        if (esAll) esAll.close();
        if (esServer) esServer.close();
        if (esClients) esClients.close();

        esAll = openStream(
          document.getElementById('panel-all'),
          document.getElementById('follow-all'),
          files,
          document.getElementById('flt-all').value
        );
        esServer = openStream(
          document.getElementById('panel-server'),
          document.getElementById('follow-server'),
          serverFiles,
          document.getElementById('flt-server').value
        );
        esClients = openStream(
          document.getElementById('panel-clients'),
          document.getElementById('follow-clients'),
          clientFiles,
          document.getElementById('flt-clients').value
        );
      }

      document.getElementById('reload').addEventListener('click', reload);
      window.addEventListener('load', reload);
    </script>
  </body>
</html>
"""

def list_log_files() -> List[str]:
    if not os.path.isdir(LOG_DIR):
        return []
    return [f for f in os.listdir(LOG_DIR) if f.endswith('.jsonl')]


def iter_jsonl(paths: List[str]) -> Iterable[str]:
    files = [open(p, 'r', encoding='utf-8') for p in paths]
    try:
        for f in files:
            f.seek(0, os.SEEK_END)
        while True:
            progressed = False
            for f in files:
                line = f.readline()
                if line:
                    progressed = True
                    yield line.rstrip("\n")
            if not progressed:
                time.sleep(POLL_INTERVAL_SEC)
    finally:
        for f in files:
            try:
                f.close()
            except Exception:
                pass


def record_matches_filter(record: Dict[str, object], flt: Dict[str, str]) -> bool:
    for k, v in flt.items():
        rv = record.get(k)
        if rv is None:
            return False
        if str(rv) != v:
            return False
    return True


@app.get("/")
def index() -> str:
    return render_template_string(INDEX_HTML)


@app.get("/dashboard")
def dashboard() -> str:
    return render_template_string(DASHBOARD_HTML)


@app.get("/files")
def files_endpoint():
    return list_log_files()


@app.get("/stream")
def stream_logs() -> Response:
    files = request.args.get("files", "")
    filter_arg = request.args.get("filter")
    selected = [f for f in files.split(",") if f] if files else list_log_files()
    paths = [os.path.join(LOG_DIR, f) for f in selected if os.path.isfile(os.path.join(LOG_DIR, f))]
    flt: Dict[str, str] = {}
    if filter_arg:
        try:
            flt = json.loads(filter_arg)
        except Exception:
            flt = {}

    def event_stream():
        for line in iter_jsonl(paths):
            try:
                obj = json.loads(line)
                if flt and not record_matches_filter(obj, flt):
                    continue
                yield f"data: {json.dumps(obj)}\n\n"
            except Exception:
                yield f"data: {line}\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=5050, threaded=True, debug=False)
