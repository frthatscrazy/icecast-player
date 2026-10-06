import json, time, threading, random, os, urllib.request, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

MESSAGES = []
LOCK = threading.Lock()
MAX_MSGS = 100
PORT = 8081
HISTORY_FILE = '/home/xeno/icecast-docker/web/track_history.json'
WIKI_API = 'https://en.wikipedia.org/w/api.php'
WIKI_REST = 'https://en.wikipedia.org/api/rest_v1/page/summary'
UA = 'PirateRadio/1.0 (chat bot; birdhau5radio.org)'

FALLBACK_FACTS = [
  'This track was recorded on a Tuesday. Tuesdays have the best takes.',
  'The bass player for this session had never heard the song before. Nailed it.',
  'This song runs at exactly 120 BPM. Prime head-nodding tempo.',
  'The hi-hat on this track was played with a whisk. True story.',
  'This track was mixed on speakers held together with duct tape.',
  'The engineer for this session drank 6 coffees. You can hear it in the mix.',
  'This song was tracked on 2-inch tape. That\'s 200 lbs of magnetic joy.',
  'The reverb on this track is from a $20 garage sale spring unit.',
  'This guitarist tuned down a half step. Makes everything sound heavier.',
  'The click track for this session was just a guy snapping his fingers.',
  'This song\'s bridge was written backwards then reversed.',
  'The producer yelled "perfect" after take one. Rare.',
  'This track was mastered by a guy with a pet parrot. The parrot approved.',
  'The drum sound on this song is 60% room mics. Big energy.',
  'This bass line was originally a keyboard part that got rewritten.',
  'The fade-out on this track is actually the engineer slowly turning down the fader.',
  'This song has exactly one edit point. Can you hear it?',
  'The vocalist for this track stood on a phone book to reach the mic.',
  'This track features a guitar amp that was dropped down a flight of stairs.',
  'The tambourine on this song was played by the producer\'s kid.',
]

HISTORY_POLL_INTERVAL = 6
last_track = None

def wiki_req(url):
  req = urllib.request.Request(url, headers={'User-Agent': UA})
  return json.loads(urllib.request.urlopen(req, timeout=5).read())

def extract_fact(data):
  ext = data.get('extract', '')
  if not ext:
    return None
  ext = ext.replace('\n', ' ').strip()
  sentences = [x.strip() for x in ext.split('. ') if x.strip()]
  if len(sentences) < 1:
    return None
  fact = '. '.join(sentences[:2]) + '.'
  if len(fact) > 400:
    fact = fact[:397] + '...'
  if len(fact) < 20:
    return None
  return fact

def fetch_fact(title):
  try:
    parts = title.split(' - ', 1)
    song = parts[-1].strip() if len(parts) > 1 else title
    artist = parts[0].strip() if len(parts) > 1 else ''

    # Try song-specific Wikipedia page first
    for query in [
      f'"{song}" {artist} song',
      f'"{song}"',
      f'{song} {artist} song',
    ]:
      data = wiki_req(f'{WIKI_API}?action=query&list=search&srsearch={urllib.parse.quote(query)}&format=json&srlimit=3')
      pages = data.get('query', {}).get('search', [])
      for p in pages[:2]:
        pt = p['title']
        summary = wiki_req(f'{WIKI_REST}/{urllib.parse.quote(pt)}')
        fact = extract_fact(summary)
        if fact:
          return fact

    # Try artist page
    if artist:
      data = wiki_req(f'{WIKI_API}?action=query&list=search&srsearch={urllib.parse.quote(artist)}&format=json&srlimit=1')
      pages = data.get('query', {}).get('search', [])
      if pages:
        pt = pages[0]['title']
        summary = wiki_req(f'{WIKI_REST}/{urllib.parse.quote(pt)}')
        fact = extract_fact(summary)
        if fact:
          return f'About {artist}: {fact}'

  except Exception:
    pass
  return None

def post_signal(text):
  msg = {
    'id': int(time.time() * 1000),
    'name': 'Signal',
    'text': text,
    'time': time.time(),
  }
  with LOCK:
    MESSAGES.append(msg)
    if len(MESSAGES) > MAX_MSGS:
      MESSAGES[:len(MESSAGES)-MAX_MSGS] = []

def signal_worker():
  global last_track
  while True:
    try:
      if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f:
          data = json.load(f)
        if isinstance(data, list) and len(data) > 0:
          latest = data[0]
          title = latest.get('title', '') if isinstance(latest, dict) else str(latest)
          if title and title != last_track:
            last_track = title
            fact = fetch_fact(title)
            if not fact:
              fact = random.choice(FALLBACK_FACTS)
            post_signal(fact)
    except Exception:
      pass
    time.sleep(HISTORY_POLL_INTERVAL)

t = threading.Thread(target=signal_worker, daemon=True)
t.start()

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
