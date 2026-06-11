import json, time, threading, random, itertools
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

RANKS = '23456789TJQKA'
SUITS = 'shdc'
SUIT_SYM = {'s': '\u2660', 'h': '\u2665', 'd': '\u2666', 'c': '\u2663'}
HAND_NAMES = ['high card','pair','two pair','three of a kind','straight','flush','full house','four of a kind','straight flush','royal flush']

GAMES = {}
LOCK = threading.Lock()

def create_deck():
  return [r+s for r in RANKS for s in SUITS]

def shuffle_deck(deck):
  random.shuffle(deck)

def rank_idx(c):
  return RANKS.index(c[0])

def suit_idx(c):
  return SUITS.index(c[1])

def eval_hand(hole, board):
  cards = hole + board
  best = None
  best_rank = -1
  for combo in itertools.combinations(cards, 5):
    score = score_5(combo)
    if score > best_rank:
      best_rank = score
      best = combo
  return best_rank, list(best)

def score_5(cards):
  ranks = sorted([rank_idx(c) for c in cards], reverse=True)
  suits = [suit_idx(c) for c in cards]
  is_flush = len(set(suits)) == 1
  is_straight, straight_high = check_straight(ranks)
  counts = {}
  for r in ranks:
    counts[r] = counts.get(r, 0) + 1
  groups = sorted(counts.values(), reverse=True)
  uniq_ranks = sorted(counts.keys(), reverse=True)

  # Royal flush
  if is_flush and is_straight and straight_high == 12:
    return 9 << 20

  # Straight flush
  if is_flush and is_straight:
    return (8 << 20) | straight_high

  # Four of a kind
  if groups == [4, 1]:
    kicker = [r for r in uniq_ranks if counts[r] == 1][0]
    quad = [r for r in uniq_ranks if counts[r] == 4][0]
    return (7 << 20) | (quad << 4) | kicker

  # Full house
  if groups == [3, 2]:
    trip = [r for r in uniq_ranks if counts[r] == 3][0]
    pair = [r for r in uniq_ranks if counts[r] == 2][0]
    return (6 << 20) | (trip << 4) | pair

  # Flush
  if is_flush:
    score = (5 << 20)
    for i, r in enumerate(ranks):
      score |= r << (4 * (4 - i))
    return score

  # Straight
  if is_straight:
    return (4 << 20) | straight_high

  # Three of a kind
  if groups == [3, 1, 1]:
    trip = [r for r in uniq_ranks if counts[r] == 3][0]
    kickers = sorted([r for r in uniq_ranks if counts[r] != 3], reverse=True)
    return (3 << 20) | (trip << 8) | (kickers[0] << 4) | kickers[1]

  # Two pair
  if groups == [2, 2, 1]:
    pairs = sorted([r for r in uniq_ranks if counts[r] == 2], reverse=True)
    kicker = [r for r in uniq_ranks if counts[r] == 1][0]
    return (2 << 20) | (pairs[0] << 8) | (pairs[1] << 4) | kicker

  # One pair
  if groups == [2, 1, 1, 1]:
    pair = [r for r in uniq_ranks if counts[r] == 2][0]
    kickers = sorted([r for r in uniq_ranks if counts[r] == 1], reverse=True)
    return (1 << 20) | (pair << 12) | (kickers[0] << 8) | (kickers[1] << 4) | kickers[2]

  # High card
  score = 0
  for i, r in enumerate(ranks):
    score |= r << (4 * (4 - i))
  return score

def check_straight(ranks):
  r = sorted(set(ranks), reverse=True)
  for i in range(len(r) - 4):
    if r[i] - r[i+4] == 4:
      return True, r[i]
  if 12 in r and 3 in r and 2 in r and 1 in r and 0 in r:
    return True, 3
  return False, -1

def hand_name(score):
  cat = score >> 20
  if cat < len(HAND_NAMES):
    return HAND_NAMES[cat]
  return 'unknown'

def cards_str(cards):
  return ''.join(cards)

def str_cards(s):
  return [s[i:i+2] for i in range(0, len(s), 2)]

class PokerGame:
  def __init__(self, coffer):
    self.id = str(int(time.time() * 1000))
    self.deck = create_deck()
    shuffle_deck(self.deck)
    self.player_stack = coffer
    self.signal_stack = 500
    self.pot = 0
    self.current_bet = 0
    self.player_bet = 0
    self.signal_bet = 0
    self.player_hand = []
    self.signal_hand = []
    self.community = []
    self.phase = 'preflop'
    self.turn = 'player'
    self.last_action = ''
    self.action_on = 'player'
    self.round_actions = 0
    self.hand_over = False
    self.result = ''
    self.coffer_delta = 0
    self.signal_acted = False
    self.player_acted = False
    self.last_raise = 10
    self.dealer = 'signal'
    self.deal_hole()

  def deal_hole(self):
    self.player_hand = [self.deck.pop(), self.deck.pop()]
    self.signal_hand = [self.deck.pop(), self.deck.pop()]
    self.post_blinds()

  def post_blinds(self):
    sb = 5
    bb = 10
    if self.dealer == 'signal':
      self.player_stack -= sb
      self.signal_stack -= bb
      self.player_bet = sb
      self.signal_bet = bb
      self.pot = sb + bb
      self.current_bet = bb
      self.last_raise = bb
      self.turn = 'player'
    else:
      self.signal_stack -= sb
      self.player_stack -= bb
      self.signal_bet = sb
      self.player_bet = bb
      self.pot = sb + bb
      self.current_bet = bb
      self.last_raise = bb
      self.turn = 'signal'
    self.action_on = self.turn
    self.player_acted = False
    self.signal_acted = False

  def get_state(self, show_signal=False):
    return {
      'id': self.id,
      'phase': self.phase,
      'turn': self.turn,
      'hand_over': self.hand_over,
      'result': self.result,
      'coffer_delta': self.coffer_delta,
      'pot': self.pot,
      'current_bet': self.current_bet,
      'player_stack': self.player_stack,
      'signal_stack': self.signal_stack,
      'player_bet': self.player_bet,
      'signal_bet': self.signal_bet,
      'player_hand': ''.join(self.player_hand),
      'signal_hand': ''.join(self.signal_hand) if show_signal else '????',
      'community': ''.join(self.community),
      'last_action': self.last_action,
      'action_on': self.action_on,
      'dealer': self.dealer,
      'player_acted': self.player_acted,
      'signal_acted': self.signal_acted,
    }

  def next_phase(self, dealer_pos=None):
    if self.hand_over:
      return
    self.player_bet = 0
    self.signal_bet = 0
    self.current_bet = 0
    self.player_acted = False
    self.signal_acted = False
    self.round_actions = 0

    if self.phase == 'preflop':
      self.phase = 'flop'
      self.deck.pop()
      self.community.extend([self.deck.pop() for _ in range(3)])
    elif self.phase == 'flop':
      self.phase = 'turn'
      self.deck.pop()
      self.community.append(self.deck.pop())
    elif self.phase == 'turn':
      self.phase = 'river'
      self.deck.pop()
      self.community.append(self.deck.pop())
    elif self.phase == 'river':
      self.showdown()
      return

    self.turn = 'player' if self.dealer == 'signal' else 'signal'
    self.action_on = self.turn

  def showdown(self):
    self.hand_over = True
    p_score, p_best = eval_hand(self.player_hand, self.community)
    s_score, s_best = eval_hand(self.signal_hand, self.community)
    if p_score > s_score:
      self.player_stack += self.pot
      self.coffer_delta = self.pot
      p_name = hand_name(p_score)
      s_name = hand_name(s_score)
      self.result = f'You win {self.pot} doubloons! ({p_name} beats {s_name})'
    elif s_score > p_score:
      self.signal_stack += self.pot
      self.coffer_delta = -self.pot
      p_name = hand_name(p_score)
      s_name = hand_name(s_score)
      self.result = f'Signal wins {self.pot}! ({s_name} beats {p_name})'
    else:
      split = self.pot // 2
      self.player_stack += split
      self.signal_stack += split
      self.coffer_delta = split - (self.pot - split)
      self.result = f'Chop! Split {self.pot} doubloons ({hand_name(p_score)})'

  def valid_actions(self):
    if self.hand_over:
      return []
    to_call = self.current_bet - (self.player_bet if self.turn == 'player' else self.signal_bet)
    stack = self.player_stack if self.turn == 'player' else self.signal_stack
    can_check = to_call == 0
    can_call = to_call > 0 and to_call <= stack
    min_raise = max(self.last_raise, self.current_bet * 2)
    can_raise = stack > to_call and (stack > min_raise or stack <= to_call)
    return {
      'can_fold': True,
      'can_check': can_check,
      'can_call': can_call,
      'can_raise': can_raise,
      'min_raise': min_raise,
      'to_call': to_call,
      'stack': stack,
    }

  def act(self, action, amount=0):
    if self.hand_over or self.turn == 'signal':
      return False
    stack = self.player_stack
    self.action_on = 'player'
    to_call = max(0, self.current_bet - self.player_bet)

    if action == 'fold':
      self.hand_over = True
      self.signal_stack += self.pot
      self.coffer_delta = -self.player_bet
      self.result = 'You folded. Signal wins the pot.'
      self.last_action = 'Player folds'
      return True

    if action == 'check':
      if to_call > 0:
        return False
      self.last_action = 'Player checks'
      self.player_acted = True
      self.round_actions += 1
      if self.round_actions >= 2 and self.signal_acted:
        self.next_phase()
      else:
        self.turn = 'signal'
      return True

    if action == 'call':
      if to_call > stack:
        amount = stack
      else:
        amount = to_call
      if amount <= 0:
        return False
      self.player_stack -= amount
      self.pot += amount
      self.player_bet += amount
      self.last_action = f'Player calls {amount}'
      self.player_acted = True
      self.round_actions += 1
      if self.round_actions >= 2 and self.signal_acted:
        self.next_phase()
      else:
        self.turn = 'signal'
      return True

    if action == 'raise':
      min_r = max(self.last_raise, self.current_bet * 2)
      if amount < min_r and amount < stack:
        return False
      if amount > stack:
        amount = stack
      total = amount
      self.player_stack -= total
      self.pot += total
      self.player_bet += total
      self.current_bet = self.player_bet
      self.last_raise = amount
      self.last_action = f'Player raises to {total}'
      self.player_acted = True
      self.round_actions = 1
      self.signal_acted = False
      self.turn = 'signal'
      return True

    if action == 'all_in':
      amount = stack
      self.player_stack = 0
      self.pot += amount
      self.player_bet += amount
      self.current_bet = max(self.current_bet, self.player_bet)
      self.last_raise = max(self.last_raise, amount)
      self.last_action = f'Player ALL IN for {amount}'
      self.player_acted = True
      self.round_actions = 1
      self.signal_acted = False
      self.turn = 'signal'
      return True

    return False

  def signal_act(self):
    if self.hand_over or self.turn != 'signal':
      return
    to_call = max(0, self.current_bet - self.signal_bet)
    stack = self.signal_stack
    pot_odds = to_call / (self.pot + to_call) if (self.pot + to_call) > 0 else 0

    # Reasonable AI
    if self.phase == 'preflop':
      cards = [rank_idx(c) for c in self.signal_hand]
      cards.sort(reverse=True)
      r1, r2 = cards[0], cards[1]
      suited = self.signal_hand[0][1] == self.signal_hand[1][1]
      paired = r1 == r2
      gap = r1 - r2

      premium = paired and r1 >= 9
      strong = (r1 >= 12 and r2 >= 11) or (paired and r1 >= 6)
      playable = (r1 >= 10 and r2 >= 8) or (paired and r1 >= 3) or (r1 >= 11 and r2 >= 9 and suited) or (gap <= 2 and suited and r1 >= 8)
      speculative = (r1 >= 8 and r2 >= 7 and suited) or (paired)

      if to_call == 0:
        if premium:
          self._signal_raise(max(self.last_raise * 3, 30))
        elif strong:
          self._signal_raise(self.last_raise * 2)
        else:
          self._signal_check()
      else:
        if to_call >= stack:
          if premium:
            self._signal_call(stack)
          else:
            self._signal_fold()
        elif premium:
          self._signal_raise(max(to_call * 3, self.last_raise * 3))
        elif strong:
          self._signal_raise(max(to_call * 2, self.last_raise * 2))
        elif playable and pot_odds < 0.3:
          self._signal_call(to_call)
        elif speculative and pot_odds < 0.15:
          self._signal_call(to_call)
        else:
          self._signal_fold()
    else:
      # Post-flop
      hand_strength, _ = eval_hand(self.signal_hand, self.community)
      cat = hand_strength >> 20
      if cat >= 6:
        if to_call == 0:
          self._signal_raise(max(self.last_raise, self.pot))
        elif to_call < stack:
          self._signal_raise(max(to_call * 2, self.pot))
        else:
          self._signal_call(to_call)
      elif cat >= 4:
        if to_call == 0:
          self._signal_raise(max(self.last_raise, self.pot // 2))
        elif pot_odds < 0.35:
          self._signal_call(to_call)
        else:
          self._signal_fold()
      elif cat >= 2:
        if to_call == 0:
          self._signal_check()
        elif pot_odds < 0.3:
          self._signal_call(to_call)
        else:
          self._signal_fold()
      else:
        # Bluff sometimes (15%)
        if to_call == 0 and random.random() < 0.15:
          self._signal_raise(self.pot // 2)
        elif to_call > stack:
          self._signal_fold()
        elif to_call > 0 and pot_odds > 0.4:
          self._signal_call(to_call)
        elif to_call > 0:
          self._signal_fold()
        else:
          self._signal_check()

  def _signal_fold(self):
    self.hand_over = True
    self.player_stack += self.pot
    self.coffer_delta = self.pot
    self.result = 'Signal folds. You win!'
    self.last_action = 'Signal folds'

  def _signal_check(self):
    self.signal_acted = True
    self.round_actions += 1
    self.last_action = 'Signal checks'
    if self.round_actions >= 2 and self.player_acted:
      self.next_phase()
    else:
      self.turn = 'player'

  def _signal_call(self, amount):
    if amount > self.signal_stack:
      amount = self.signal_stack
    self.signal_stack -= amount
    self.pot += amount
    self.signal_bet += amount
    self.last_action = f'Signal calls {amount}'
    self.signal_acted = True
    self.round_actions += 1
    if self.round_actions >= 2 and self.player_acted:
      self.next_phase()
    else:
      self.turn = 'player'

  def _signal_raise(self, amount):
    if amount > self.signal_stack:
      amount = self.signal_stack
    total = amount
    self.signal_stack -= total
    self.pot += total
    self.signal_bet += total
    self.current_bet = self.signal_bet
    self.last_raise = amount
    self.last_action = f'Signal raises to {total}'
    self.signal_acted = True
    self.round_actions = 1
    self.player_acted = False
    self.turn = 'player'

class PokerHandler(BaseHTTPRequestHandler):
  def do_GET(self):
    parsed = urlparse(self.path)
    qs = parse_qs(parsed.query)
    if parsed.path == '/state':
      gid = qs.get('id', [''])[0]
      with LOCK:
        game = GAMES.get(gid)
        if not game:
          self.send_json({'ok': False, 'error': 'no game'}, 404)
          return
        self.send_json({'ok': True, **game.get_state(show_signal=game.hand_over)})
    elif parsed.path == '/new_hand':
      coffer = min(int(qs.get('coffer', ['500'])[0]), 5000)
      g = PokerGame(coffer)
      if g.player_stack <= 0:
        self.send_json({'ok': False, 'error': 'broke'})
        return
      with LOCK:
        GAMES[g.id] = g
      if g.turn == 'signal':
        g.signal_act()
      self.send_json({'ok': True, **g.get_state(show_signal=g.hand_over)})
    else:
      self.send_json({'ok': False, 'error': 'not found'}, 404)

  def do_POST(self):
    parsed = urlparse(self.path)
    qs = parse_qs(parsed.query)
    body = {}
    try:
      length = int(self.headers.get('Content-Length', 0))
      if length:
        body = json.loads(self.rfile.read(length))
    except:
      pass

    with LOCK:
      if parsed.path == '/new_hand':
        coffer = min(int(qs.get('coffer', ['500'])[0]), 5000)
        game = PokerGame(coffer)
        if game.player_stack <= 0:
          self.send_json({'ok': False, 'error': 'broke'})
          return
        GAMES[game.id] = game
        # If dealer signal, signal acts first post-blind
        if game.turn == 'signal':
          game.signal_act()
        self.send_json({'ok': True, **game.get_state(show_signal=game.hand_over)})

      elif parsed.path == '/act':
        gid = body.get('game_id', '')
        game = GAMES.get(gid)
        if not game:
          self.send_json({'ok': False, 'error': 'no game'}, 404)
          return
        action = body.get('action', '')
        amount = int(body.get('amount', 0))
        ok = game.act(action, amount)
        if not ok:
          self.send_json({'ok': False, 'error': 'invalid action'})
          return
        # Run signal AI if needed
        if not game.hand_over and game.turn == 'signal':
          game.signal_act()
        # If signal acted and no phase change, check if we need another signal act
        safety = 0
        while not game.hand_over and game.turn == 'signal' and safety < 10:
          game.signal_act()
          safety += 1
        self.send_json({'ok': True, **game.get_state(show_signal=game.hand_over)})

      else:
        self.send_json({'ok': False, 'error': 'not found'}, 404)

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

server = HTTPServer(('0.0.0.0', 8082), PokerHandler)
print('poker server on 8082')
server.serve_forever()
