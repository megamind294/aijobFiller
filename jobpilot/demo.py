"""Local-only demo: submissions go nowhere and data is not persisted."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

HTML='''<!doctype html><html lang="en"><meta charset="utf-8"><title>JobPilot Local Demo</title>
<style>body{font:16px system-ui;background:#eef2f7;color:#13233a;margin:40px auto;max-width:700px}main{background:white;padding:32px;border-radius:16px}label{display:block;margin:16px 0 5px}input:not([type=checkbox]),select{box-sizing:border-box;padding:10px;width:100%;border:1px solid #abb8c9;border-radius:6px}button{background:#17365d;color:white;padding:14px 24px;border:0;border-radius:6px;margin-top:20px}.note{color:#475569}</style>
<main><p class="note">LOCAL DEMO · No real application is sent</p><h1>React Developer</h1><p>Example Company · Krakow, Poland</p>
<form method="post" action="/apply" enctype="multipart/form-data">
<label for="first">First Name</label><input id="first" name="first_name" required>
<label for="last">Last Name</label><input id="last" name="last_name" required>
<label for="email">Email address</label><input id="email" name="email" type="email" required>
<label for="phone">Phone number</label><input id="phone" name="phone" type="tel" required>
<label for="city">City</label><input id="city" name="city">
<label for="country">Country</label><select id="country" name="country"><option value="">Choose</option><option value="PL">Poland</option><option value="IN">India</option></select>
<label for="resume">Resume / CV</label><input id="resume" name="resume" type="file" accept=".pdf" required>
<label for="sponsor">Do you require sponsorship?</label><select id="sponsor" name="sponsor" required><option value="">Choose yourself</option><option value="yes">Yes</option><option value="no">No</option></select>
<label><input id="consent" name="consent" type="checkbox" required> I reviewed this demo application.</label>
<button type="submit">Submit demo application</button></form></main></html>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(HTML.encode())
    def do_POST(self):
        if self.path!='/apply': self.send_error(404); return
        size=int(self.headers.get('Content-Length','0'))
        if size>10_000_000: self.send_error(413); return
        self.rfile.read(size)
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers()
        self.wfile.write(b'<html><h1 id="receipt">Application received</h1><p>Demo receipt DEMO-001. No employer was contacted.</p></html>')
    def log_message(self,*args): pass

def make_server(port=8765): return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def serve(port):
    server=make_server(port)
    print(f'Local demo: http://127.0.0.1:{server.server_port} (Ctrl+C to stop)',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
