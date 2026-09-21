"""Loopback-only disposable web fixtures with independently counted requests."""

import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class WebFixture:
    def __init__(self, forbidden_origin=''):
        self.forbidden_origin = forbidden_origin
        self.counts = Counter()
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                with owner.lock:
                    owner.counts[parsed.path] += 1
                query = parse_qs(parsed.query)
                if parsed.path == '/slow':
                    time.sleep(.5)
                if parsed.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', query.get('to', ['/app'])[0])
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                label = query.get('label', ['fixture'])[0][:40]
                extra = ''
                if parsed.path == '/unsafe':
                    f = owner.forbidden_origin
                    extra = ('<img src="'+f+'/image"><iframe src="'+f+'/frame"></iframe>'
                             '<script>fetch('+json.dumps(f+'/fetch')+').catch(()=>{});'
                             'new WebSocket('+json.dumps(f.replace('http:', 'ws:')+'/socket')+');'
                             'window.open('+json.dumps(f+'/popup')+');</script>')
                if parsed.path == '/redirect-attacks':
                    from urllib.parse import quote
                    destination = owner.forbidden_origin + '/multi-hop-target'
                    inner = owner.origin+'/redirect?to='+quote(destination, safe='')
                    chain = owner.origin+'/redirect?to='+quote(inner, safe='')
                    extra = ('<img src="'+chain+'"><iframe src="'+chain+'"></iframe>'
                             '<script>fetch('+json.dumps(chain)+').catch(()=>{});'
                             'window.open('+json.dumps(chain)+');</script>')
                page = '''<!doctype html><html lang="zh"><meta charset="utf-8">
                <title>BF Browser Fixture</title>
                <style>body{font:20px Arial,"Microsoft YaHei",sans-serif;margin:64px;color:#18283c}
                main{max-width:900px;border:1px solid #9caabd;padding:32px}button,input{font:inherit;padding:10px}
                h1{font-size:30px}p{line-height:1.8}</style><main>
                <h1>BF 网页自动化验证</h1><p>Independent browser observation fixture</p>
                <p id="scope" data-testid="scope"></p>
                <label for="item">测试输入框</label><input id="item" data-testid="item">
                <button data-testid="apply">Apply</button>
                <p data-bf-private>Private fixture text must be masked in evidence</p>
                <label for="secret">Password</label><input id="secret" type="password" value="TEST_ONLY_PASSWORD">
                </main><script>
                const label=LABEL;
                if(!localStorage.getItem('bf-scope')) localStorage.setItem('bf-scope',label);
                if(!document.cookie.includes('bf-scope='))document.cookie='bf-scope='+encodeURIComponent(label)+'; SameSite=Strict';
                document.getElementById('scope').innerText='Scope: '+localStorage.getItem('bf-scope')+' | '+document.cookie;
                document.title='BF Browser '+localStorage.getItem('bf-scope');
                </script>EXTRA</html>'''.replace('LABEL', json.dumps(label)).replace('EXTRA', extra)
                if parsed.path == '/iframe-private':
                    page = ('<!doctype html><title>Private frame fixture</title><body style="margin:0">'
                            '<iframe src="/private-child" style="border:0;width:500px;height:300px"></iframe></body>')
                if parsed.path == '/private-child':
                    page = ('<!doctype html><body style="margin:0"><div data-bf-private '
                            'style="width:300px;height:100px;background:rgb(0,255,0)">TEST_PRIVATE_IFRAME</div>'
                            '<input type="password" value="FIXTURE_ONLY"></body>')
                data = page.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def total_requests(self):
        with self.lock:
            return sum(self.counts.values())
