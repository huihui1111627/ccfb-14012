"""Monitoring web UI: every association's negotiation trace and transfer
progress, served with the standard library only.

Run:  python -m dicom_gw.web [--http 8080] [--dicom 11112]
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import uid as U
from .gateway import Gateway, Monitor

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>DICOM Gateway Monitor</title>
<style>
 body { font: 13px/1.5 -apple-system, "PingFang SC", monospace; margin: 0;
        background: #0f1419; color: #d5dbe1; }
 header { padding: 10px 16px; background: #1a2230; display: flex;
          justify-content: space-between; align-items: baseline; }
 h1 { font-size: 15px; margin: 0; }
 #stats { color: #7f8b99; }
 table { border-collapse: collapse; width: 100%; }
 th, td { text-align: left; padding: 4px 10px; border-bottom: 1px solid #232c3a;
          vertical-align: top; }
 th { color: #7f8b99; font-weight: normal; position: sticky; top: 0;
      background: #0f1419; }
 tr.rec { cursor: pointer; }
 tr.rec:hover { background: #18202c; }
 .in  { color: #6cc7ff; } .out { color: #ffc46b; }
 .st-open { color: #9fe870; } .st-released { color: #7f8b99; }
 .st-aborted, .st-error { color: #ff6b6b; }
 .bar { background: #232c3a; height: 6px; border-radius: 3px; min-width: 80px; }
 .bar > div { background: #4f9cf0; height: 6px; border-radius: 3px; }
 .detail { display: none; background: #111824; }
 .detail pre { margin: 0; padding: 8px 16px; white-space: pre-wrap;
               font-size: 12px; color: #a9b6c4; max-height: 320px;
               overflow: auto; }
 .ev-negotiate { color: #9fe870; } .ev-abort, .ev-error { color: #ff6b6b; }
 .ev-transcode { color: #ffc46b; } .ev-send { color: #6cc7ff; }
 .ev-recv { color: #b39df0; }
</style>
</head>
<body>
<header><h1>DICOM 网关监视器</h1><span id="stats">连接中…</span></header>
<table>
<thead><tr><th>#</th><th>方向</th><th>对端</th><th>角色</th><th>Calling</th>
<th>Called</th><th>协商结果</th><th>发送</th><th>接收</th><th>状态</th></tr></thead>
<tbody id="rows"></tbody>
</table>
<script>
const tbody = document.getElementById('rows');
const stats = document.getElementById('stats');
let open = new Set();

function fmtBytes(n){ if(n>1048576) return (n/1048576).toFixed(1)+' MB';
  if(n>1024) return (n/1024).toFixed(1)+' KB'; return n+' B'; }
function fmtTime(t){ const d=new Date(t*1000);
  return d.toTimeString().slice(0,8); }
function esc(s){ const d=document.createElement('div'); d.textContent=s;
  return d.innerHTML; }

async function refresh(){
  let list;
  try { list = await (await fetch('/api/associations')).json(); }
  catch(e){ stats.textContent='连接断开,重试中…'; return; }
  stats.textContent = list.length + ' 条关联 · 更新于 ' +
    new Date().toLocaleTimeString();
  tbody.innerHTML='';
  for(const rec of list.slice().reverse()){
    const tr=document.createElement('tr'); tr.className='rec';
    const dir=rec.direction==='in'?'◀ 入':'▶ 出';
    const ts=rec.transfer_syntaxes.map(esc).join('<br>');
    tr.innerHTML='<td>'+rec.id+'</td>'+
      '<td class="'+rec.direction+'">'+dir+'</td>'+
      '<td>'+esc(rec.peer)+'</td><td>'+esc(rec.role)+'</td>'+
      '<td>'+esc(rec.calling_ae)+'</td><td>'+esc(rec.called_ae)+'</td>'+
      '<td>'+ts+'</td>'+
      '<td>'+fmtBytes(rec.progress_send)+'</td>'+
      '<td>'+fmtBytes(rec.progress_recv)+'</td>'+
      '<td class="st-'+rec.status+'">'+rec.status+'</td>';
    const det=document.createElement('tr'); det.className='detail';
    const td=document.createElement('td'); td.colSpan=10;
    const pre=document.createElement('pre');
    pre.innerHTML=rec.events.map(e=>{
      const cls='ev-'+e[1];
      return '<span class="'+cls+'">'+fmtTime(e[0])+'  '+esc(e[2])+'</span>';
    }).join('\\n');
    td.appendChild(pre); det.appendChild(td);
    if(open.has(rec.id)) det.style.display='table-row';
    tr.onclick=()=>{ const s=det.style.display==='table-row'?'none':'table-row';
      det.style.display=s;
      if(s==='table-row') open.add(rec.id); else open.delete(rec.id); };
    tbody.appendChild(tr); tbody.appendChild(det);
  }
}
setInterval(refresh, 1000); refresh();
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    monitor = None

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/associations":
            recs = [r.to_dict() for r in self.monitor.list()]
            self._send(200, json.dumps(recs))
        elif self.path.startswith("/api/association/"):
            rid = int(self.path.rsplit("/", 1)[1])
            for r in self.monitor.list():
                if r.id == rid:
                    self._send(200, json.dumps(r.to_dict()))
                    return
            self._send(404, "{}")
        else:
            self._send(404, "{}")


def serve(monitor, http_port=8080):
    handler = type("Handler", (_Handler,), {"monitor": monitor})
    httpd = ThreadingHTTPServer(("127.0.0.1", http_port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def main():
    ap = argparse.ArgumentParser(description="DICOM gateway with monitor UI")
    ap.add_argument("--http", type=int, default=8080)
    ap.add_argument("--dicom", type=int, default=11112)
    ap.add_argument("--ae", default="GATEWAY")
    args = ap.parse_args()

    from .gateway import DeviceProfile
    profile = DeviceProfile("gateway", args.ae, list(U.ALL_TRANSFER_SYNTAXES))
    monitor = Monitor()
    gw = Gateway(ae_title=args.ae, port=args.dicom,
                 scp_contexts=profile.scp_contexts(), monitor=monitor)
    gw.start()
    serve(monitor, args.http)
    print("DICOM SCP on port %d (AE %s), monitor on http://127.0.0.1:%d"
          % (gw.port, args.ae, args.http))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        gw.stop()


if __name__ == "__main__":
    main()
