#!/usr/bin/env python3
"""Minimal album-art receiver + track history logger for Pirate Radio.

POST /upload    — receive cover art (multipart field "file", X-Api-Key)
GET /cover/current.jpg  — serve latest image (no-cache)
GET /health              — health check
GET /history             — track play history (JSON)
"""

import os, sys, json, hashlib, struct, zlib, threading, time, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from io import BytesIO

PORT = 8080
API_KEY = 'pirate2024'
COVER_DIR = '/home/xeno/icecast-docker/web/cover'
COVER_PATH = os.path.join(COVER_DIR, 'current.jpg')
COVER_TMP = os.path.join(COVER_DIR, 'current.jpg.tmp')
FALLBACK_PATH = os.path.join(COVER_DIR, '_fallback.png')
MAX_SIZE = 5 * 1024 * 1024
HISTORY_FILE = '/home/xeno/icecast-docker/web/track_history.json'
HISTORY_LIMIT = 100
STATUS_URL = 'http://localhost:8000/status-json.xsl'

track_history = []
track_lock = threading.Lock()
_last_title = ''

# ── fallback image (16x16 dark PNG) ──────────────────────────────────
def _make_fallback():
    w = h = 16
    raw = b''
    for y in range(h):
        row = b'\x00' * w * 3
        raw += b'\x00' + row + b'\x00\xff'
    raw += b'\x00'
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', ihdr)
    png += chunk(b'IDAT', zlib.compress(raw))
    png += chunk(b'IEND', b'')
    return png

def ensure_fallback():
    os.makedirs(COVER_DIR, exist_ok=True)
    if not os.path.exists(FALLBACK_PATH):
        with open(FALLBACK_PATH + '.tmp', 'wb') as f:
            f.write(_make_fallback())
        os.rename(FALLBACK_PATH + '.tmp', FALLBACK_PATH)

# ── track history poller ──────────────────────────────────────────────
def _fix_title(s):
    try: return s.encode('latin-1').decode('utf-8')
    except: return s

def parse_status(data):
    src = data.get('icestats', {}).get('source', {})
    if isinstance(src, list):
        src = src[0] if src else {}
    return _fix_title(src.get('title', '') or '')

def poll_tracks():
    global _last_title
    while True:
        try:
            r = urllib.request.urlopen(STATUS_URL, timeout=5)
            title = parse_status(json.loads(r.read()))
            if title and title != _last_title:
                _last_title = title
                entry = {'time': time.strftime('%H:%M:%S'), 'title': title}
                with track_lock:
                    track_history.insert(0, entry)
                    if len(track_history) > HISTORY_LIMIT:
                        del track_history[HISTORY_LIMIT:]
                    try:
                        with open(HISTORY_FILE, 'w') as f:
                            json.dump(track_history, f)
                    except OSError:
                        pass
        except Exception:
            pass
        time.sleep(5)

# ── handler ──────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._json(code, {'ok': False, 'error': msg})

    def _serve_file(self, path, mime):
        if not os.path.exists(path):
            self._err(404, 'Not found')
            return
        try:
            with open(path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self._err(500, 'Read failed')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Api-Key, Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'ok')
            return
        if parsed.path == '/cover/current.jpg':
            self._serve_file(COVER_PATH, 'image/jpeg')
            return
        if parsed.path == '/history':
            with track_lock:
                h = list(track_history)
            self._json(200, {'ok': True, 'history': h})
            return
        self._err(404, 'Not found')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != '/upload':
            self._err(404, 'Not found')
            return
        key = self.headers.get('X-Api-Key', '')
        if key != API_KEY:
            sys.stderr.write('[album_art] 401 bad api key\n')
            self._err(401, 'Unauthorized')
            return
        clen = int(self.headers.get('Content-Length', 0))
        if clen == 0:
            self._err(400, 'Empty request')
            return
        if clen > MAX_SIZE:
            self._err(413, 'File too large')
            return
        ctype = self.headers.get('Content-Type', '')
        body = self.rfile.read(clen)
        data = None
        if 'multipart/form-data' in ctype:
            try:
                _, raw_boundary = ctype.split('boundary=', 1)
                boundary = raw_boundary.strip().strip('"')
                parts = body.split(('--' + boundary).encode())
                for part in parts:
                    if b'name="file"' in part:
                        header_end = part.find(b'\r\n\r\n')
                        if header_end > 0:
                            data = part[header_end + 4:]
                            data = data.rstrip(b'\r\n- ')
                            break
                if not data:
                    self._err(400, 'No file field')
                    return
            except Exception as e:
                self._err(400, 'Parse error: ' + str(e))
                return
        else:
            self._err(400, 'Expected multipart/form-data')
            return
        if not data:
            self._err(400, 'Empty file')
            return
        try:
            ensure_fallback()
            with open(COVER_TMP, 'wb') as f:
                f.write(data)
            os.rename(COVER_TMP, COVER_PATH)
            sz = len(data)
            sys.stderr.write('[album_art] 200 uploaded %d bytes\n' % sz)
            self._json(200, {'ok': True, 'size': sz})
        except OSError as e:
            sys.stderr.write('[album_art] 500 write error: %s\n' % e)
            self._err(500, 'Write failed')

    def log_message(self, fmt, *args):
        sys.stderr.write('[album_art] %s\n' % (fmt % args))

# ── main ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    ensure_fallback()
    t = threading.Thread(target=poll_tracks, daemon=True)
    t.start()
    print('Album art + history server :%d' % PORT)
    print('  POST /upload        (X-Api-Key: %s)' % API_KEY)
    print('  GET  /cover/current.jpg')
    print('  GET  /history')
    server = HTTPServer(('0.0.0.0', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutdown')
        server.shutdown()
