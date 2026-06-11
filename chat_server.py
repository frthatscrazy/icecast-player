import json, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

MESSAGES = []
LOCK = threading.Lock()
MAX_MSGS = 100
PORT = 8081

class ChatHandler(BaseHTTPRequestHandler):
  def do_GET(self):
    qs = parse_qs(urlparse(self.path).query)
    since = int(qs.get('since', ['0'])[0])
    with LOCK:
      new = [m for m in MESSAGES if m['id'] > since]
    self.send_json({'messages': new})

  def do_POST(self):
    length = int(self.headers.get('Content-Length', 0))
    body = self.rfile.read(length)
    try:
      data = json.loads(body)
      text = data.get('text', '').strip()
      name = data.get('name', 'Anonymous Pirate').strip()
      if not name: name = 'Anonymous Pirate'
      if not text:
        self.send_json({'ok': False, 'error': 'empty'}, 400)
        return
      msg = {'id': int(time.time() * 1000), 'name': name[:20], 'text': text[:200], 'time': time.time()}
      with LOCK:
        MESSAGES.append(msg)
        if len(MESSAGES) > MAX_MSGS:
          MESSAGES[:len(MESSAGES)-MAX_MSGS] = []
      self.send_json({'ok': True, 'message': msg})
    except Exception as e:
      self.send_json({'ok': False, 'error': str(e)}, 400)

  def send_json(self, data, code=200):
    self.send_response(code)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Access-Control-Allow-Origin', '*')
    self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    self.send_header('Access-Control-Allow-Headers', 'Content-Type')
    self.end_headers()
    self.wfile.write(json.dumps(data).encode())

  def do_OPTIONS(self):
    self.send_json({})

  def log_message(self, *a):
    pass

server = HTTPServer(('0.0.0.0', PORT), ChatHandler)
print(f'chat server on {PORT}')
server.serve_forever()
