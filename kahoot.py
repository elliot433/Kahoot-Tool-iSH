#!/usr/bin/env python3
import os, re, sys, json, time, random, threading, subprocess, ssl, socket, base64, tempfile
from urllib.parse import quote
import requests, websocket
requests.packages.urllib3.disable_warnings()
websocket.enableTrace(False)

# iSH fix: silence unsupported setsockopt calls (errno 22)
_orig_setsockopt = socket.socket.setsockopt
def _safe_setsockopt(self, *a, **kw):
    try: return _orig_setsockopt(self, *a, **kw)
    except OSError: pass
socket.socket.setsockopt = _safe_setsockopt

# ── ANSI ─────────────────────────────────────────────────────────────────────
R="\033[0m"; B="\033[1m"; DIM="\033[2m"
G="\033[92m"; RD="\033[91m"; Y="\033[93m"
BL="\033[94m"; M="\033[95m"; C="\033[96m"; W="\033[97m"

def clr(): os.system("clear")

BANNER = f"""
{M}{B}
 ██╗  ██╗ █████╗ ██╗  ██╗ ██████╗  ██████╗ ████████╗
 ██║ ██╔╝██╔══██╗██║  ██║██╔═══██╗██╔═══██╗╚══██╔══╝
 █████╔╝ ███████║███████║██║   ██║██║   ██║   ██║
 ██╔═██╗ ██╔══██║██╔══██║██║   ██║██║   ██║   ██║
 ██║  ██╗██║  ██║██║  ██║╚██████╔╝╚██████╔╝   ██║
 ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝   ╚═╝   {R}
{DIM}          Auto-Answer  ·  Flood  ·  Spam{R}
"""

# ── Constants ─────────────────────────────────────────────────────────────────
SESSION_URL = "https://kahoot.it/reserve/session/{}/?{}"
QUIZ_URL    = "https://kahoot.it/rest/kahoots/{}"
WS_URL      = "wss://kahoot.it/cometd/{}/{}"
UA          = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15"
HEADERS     = {"User-Agent": UA, "Accept": "application/json", "Origin": "https://kahoot.it"}
CHOICE_ICONS = ["🔴", "🔵", "🟡", "🟢"]
FUNNY_NAMES = [
    "FBI_Van_#4","YourTeacher","TotallyHuman","NotABot_69","Area51Guard",
    "IForgotMyName","WifiPassword","NPC_Player","undefined","NULL",
    "GodMode","AnswerBot","1337H4x0r","PleaseIgnore","TheChosenOne",
    "SlayQueen","IAmReal","BrainRot420","KahootKing","SchoolPC",
    "AdminAdmin","DROP_TABLE","HelloWorld","JustViewing","MathIsEasy",
]

# ── Challenge Solver ──────────────────────────────────────────────────────────
def _py_solve(js: str) -> str:
    """Pure-Python solver for all known Kahoot challenge patterns."""

    # ── Pattern A: var offset = "string"; XOR accumulator loop ───────────────
    m = re.search(r'var\s+offset\s*=\s*["\']([^"\']+)["\']', js)
    if m:
        offset_str = m.group(1)
        # Only apply XOR loop if the JS actually uses a position-based accumulator
        # (has no fromCharCode numeric-transform pattern)
        if 'charCodeAt' not in js:
            p = q = 0
            result = []
            for i, c in enumerate(offset_str):
                p = (p + q) % 256
                q = (ord(c) + i + q) % 256
                result.append(chr(ord(c) ^ p))
            key = "".join(result)
            if key:
                return key

    # ── Pattern C: decode.call(this,'token'); function decode(message) {
    #              var offset = ARITHMETIC_EXPR;
    #              return replace(message,/./g,function(char,position){
    #                return String.fromCharCode(((char.charCodeAt(0)*position)+offset)%MOD+BASE); }); }
    token_m  = re.search(r"decode\.call\(this,\s*['\"](.+?)['\"]", js, re.DOTALL)
    offset_m = re.search(r'var\s+offset\s*=\s*([^;]+);', js)
    if token_m and offset_m:
        token_str  = token_m.group(1)
        offset_raw = offset_m.group(1).strip()
        try:
            # Strip all Unicode whitespace/control chars, keep only ASCII
            clean = "".join(c if ord(c) < 128 else " " for c in offset_raw)
            # Replace any identifier with 0 (pure arithmetic only)
            safe = re.sub(r'[A-Za-z_]\w*', '0', clean)
            offset_val = int(eval(safe))
        except Exception:
            offset_val = None

        if offset_val is not None:
            # Extract % MOD and + BASE from the fromCharCode expression
            fc_m = re.search(r'%\s*(\d+)\s*\+\s*(\d+)', js)
            mod  = int(fc_m.group(1)) if fc_m else 77
            base = int(fc_m.group(2)) if fc_m else 48
            result = ""
            for i, char in enumerate(token_str):
                result += chr((ord(char) * i + offset_val) % mod + base)
            if result:
                return result

    # ── Pattern B: decode.call(this,'token', function(x){ return expr; }) ────
    tm = re.search(r"decode\.call\(this,\s*['\"](.+?)['\"](?=\s*,)", js, re.DOTALL)
    fm = re.search(r'function\s*\(\s*(\w+)\s*\)\s*\{(.+?)\}', js, re.DOTALL)
    if tm and fm:
        token_str = tm.group(1)
        param     = fm.group(1)
        body      = fm.group(2)
        var_vals  = {}
        for v in re.finditer(r'(?:var\s+)?(\w+)\s*=\s*(\d+)\s*;', body):
            if v.group(1) != param:
                var_vals[v.group(1)] = int(v.group(2))
        ret_m = re.search(r'return\s+(.+?);', body)
        if ret_m:
            expr_tpl = ret_m.group(1).strip()
            result = ""
            for c in token_str:
                expr = expr_tpl
                for var, val in var_vals.items():
                    expr = re.sub(r'\b' + var + r'\b', str(val), expr)
                expr = re.sub(r'\b' + param + r'\b', str(ord(c)), expr)
                try:    result += chr(int(eval(expr)) % 256)
                except: result += c
            if result:
                return result

    return ""

def solve_challenge(js: str) -> str:
    # Primary: Node.js with Kahoot helper stubs injected
    try:
        helpers = r"""
// Kahoot challenge helpers
function decode(token, solver) {
    var r = '';
    for (var i = 0; i < token.length; i++) {
        r += String.fromCharCode(solver(token.charCodeAt(i)));
    }
    return r;
}
var _ = {
    map: function(col, fn) {
        if (typeof col === 'string') col = col.split('');
        return col.map(fn);
    },
    replace: function(s, re, fn) { return s.replace(re, fn); },
    isNaN: isNaN
};
"""
        script = helpers + """
var res = "";
try { res = (function(){ return (CHALLENGE_PLACEHOLDER); })(); } catch(_a) {}
if (!res || typeof res !== "string") {
  try {
    CHALLENGE_PLACEHOLDER
    if (typeof challenge === "function") res = challenge();
    else if (typeof challenge !== "undefined") res = String(challenge);
  } catch(_b) {}
}
process.stdout.write(typeof res === "string" ? res : "");
""".replace("CHALLENGE_PLACEHOLDER", js)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js',
                                            delete=False, encoding='utf-8') as f:
                f.write(script)
                tmp = f.name
            out = subprocess.run(["node", tmp],
                                 capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
        finally:
            if tmp:
                try: os.unlink(tmp)
                except Exception: pass
    except Exception:
        pass

    # Fallback A: pure Python pattern solver (works without Node.js)
    py = _py_solve(js)
    if py:
        return py

    # Fallback B: generic Python regex solver
    try:
        key_m = re.search(r'decode\.call\(this,\s*["\']([^"\']+)["\']', js)
        if not key_m:
            key_m = re.search(r'["\']([A-Za-z0-9+/=]{20,})["\']', js)
        if not key_m:
            return ""
        key = key_m.group(1)

        func_m = re.search(r'function\s*\(\s*(\w)\s*\)\s*\{[^}]*return\s+(.+?)[\s;]*\}', js)
        if not func_m:
            return ""
        param, expr_tpl = func_m.group(1), func_m.group(2).strip()

        var_vals = {m.group(1): int(m.group(2))
                    for m in re.finditer(r'var\s+(\w+)\s*=\s*(\d+)', js)}

        result = ""
        for ch in key:
            expr = expr_tpl
            for var, val in var_vals.items():
                expr = re.sub(r'\b' + var + r'\b', str(val), expr)
            expr = re.sub(r'\b' + param + r'\b', str(ord(ch)), expr)
            try:
                result += chr(int(eval(expr)) % 256)
            except Exception:
                result += ch
        return result
    except Exception:
        return ""

def xor_decode(token: str, key: str) -> str:
    if not key:
        return token
    return "".join(chr(ord(a) ^ ord(key[i % len(key)])) for i, a in enumerate(token))

# ── Session ───────────────────────────────────────────────────────────────────
_session = requests.Session()

def get_session(pin: str):
    ts = int(time.time() * 1000)
    try:
        # Get base cookies from Kahoot homepage first
        _session.get("https://kahoot.it/", headers=HEADERS, timeout=8)
    except Exception:
        pass
    try:
        r = _session.get(SESSION_URL.format(pin, ts), headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return None, None, None, None, "Game not found or not started"
        if r.status_code != 200:
            return None, None, None, None, f"HTTP {r.status_code}"
        data              = r.json()
        challenge_js      = data.get("challenge", "")
        gameserver        = r.headers.get("x-kahoot-gameserver", "")
        session_token_b64 = r.headers.get("x-kahoot-session-token", "")

        # Kahoot protocol (2024+):
        #   challenge_result = decode(token_str) → used as XOR key
        #   WebSocket token  = XOR(base64decode(x-kahoot-session-token), challenge_result)
        challenge_result = solve_challenge(challenge_js) if challenge_js else ""
        token = ""
        if challenge_result and session_token_b64:
            try:
                st_bytes = base64.b64decode(session_token_b64 + "==")
                token = "".join(
                    chr(b ^ ord(challenge_result[i % len(challenge_result)]))
                    for i, b in enumerate(st_bytes))
            except Exception:
                pass

        # Fallback: use session_token directly if XOR failed
        if not token:
            token = session_token_b64

        live_game_id = pin

        # Build WebSocket base URL from gameserver header
        if gameserver:
            gs = gameserver.strip().rstrip("/")
            if gs.startswith("https://"):
                gs = gs[len("https://"):]
            elif gs.startswith("http://"):
                gs = gs[len("http://"):]
            if gs.startswith("wss://") or gs.startswith("ws://"):
                ws_base = gs
            else:
                ws_base = "wss://" + gs
        else:
            ws_base = "wss://kahoot.it"

        cookies = "; ".join(f"{k}={v}" for k, v in _session.cookies.items())

        src = "challenge" if (challenge_js and token) else ("session_token" if session_token else "none")
        print(f"  {DIM}token source: {src}  →  {token[:16]}…  WS: {ws_base}/cometd/{live_game_id}/{token[:8]}…{R}")

        return token, live_game_id, ws_base, cookies, None
    except Exception as e:
        return None, None, None, None, str(e)

# ── Answer Fetcher ────────────────────────────────────────────────────────────
def fetch_answers(kahoot_id: str) -> dict:
    """Fetch correct answer index per question from Kahoot public API."""
    if not kahoot_id:
        return {}
    try:
        r = requests.get(QUIZ_URL.format(kahoot_id), headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json()
        answers = {}
        questions = data.get("questions", data.get("kahoot", {}).get("questions", []))
        for i, q in enumerate(questions):
            choices = q.get("choices", [])
            for j, ch in enumerate(choices):
                if ch.get("correct", False):
                    answers[i] = j
                    break
        return answers
    except Exception:
        return {}

# ── Bot ───────────────────────────────────────────────────────────────────────
TIMING_RANGES = {
    "fast":   (0.3,  1.5),
    "medium": (3.0,  7.0),
    "slow":   (9.0, 16.0),
}

class KahootBot:
    def __init__(self, name, token, session_id, pin,
                 strategy="random", timing="fast", silent=False, stats=None, answer_map=None, cookies="", ws_base="wss://kahoot.it"):
        self.name       = name
        self.token      = token
        self.session_id = session_id
        self.pin        = pin
        self.ws_base    = ws_base
        self.strategy   = strategy
        self.timing     = timing
        self.silent     = silent
        self.stats      = stats
        self.answer_map = answer_map or {}
        self.cookies    = cookies
        self.ws         = None
        self.client_id  = None
        self.msg_id     = 0
        self.joined     = False
        self.running    = False
        self.answers    = 0
        self.correct    = 0

    def _log(self, msg):
        if not self.silent:
            name_col = f"{M}{B}{self.name[:14].ljust(14)}{R}"
            print(f"  {name_col} {msg}")

    def _id(self):
        self.msg_id += 1
        return str(self.msg_id)

    def _send(self, payload):
        try:
            self.ws.send(json.dumps(payload))
        except Exception:
            pass

    def _handshake(self):
        self._send([{"channel": "/meta/handshake", "version": "1.0",
                     "minimumVersion": "1.0",
                     "supportedConnectionTypes": ["websocket"],
                     "id": self._id(),
                     "ext": {"ack": True, "timesync": {
                         "tc": int(time.time()*1000), "l": 0, "o": 0}}}])

    def _meta_connect(self):
        self._send([{"channel": "/meta/connect",
                     "clientId": self.client_id,
                     "connectionType": "websocket",
                     "id": self._id(),
                     "ext": {"ack": 0, "timesync": {
                         "tc": int(time.time()*1000), "l": 0, "o": 0}}}])

    def _subscribe(self, channel):
        self._send([{"channel": "/meta/subscribe",
                     "clientId": self.client_id,
                     "subscription": channel,
                     "id": self._id()}])

    def _join(self):
        self._send([{"channel": "/service/controller",
                     "clientId": self.client_id,
                     "id": self._id(),
                     "data": {
                         "type": "login",
                         "gameid": self.session_id,
                         "host": "kahoot.it",
                         "name": self.name,
                         "content": json.dumps({"device": {
                             "userAgent": UA,
                             "screen": {"width": 390, "height": 844}}})}}])

    def _answer(self, choice: int, idx: int):
        self._send([{"channel": "/service/controller",
                     "clientId": self.client_id,
                     "id": self._id(),
                     "data": {
                         "gameid": self.session_id,
                         "host": "kahoot.it",
                         "type": "message",
                         "id": 6,
                         "content": json.dumps({
                             "choice": choice,
                             "meta": {"lag": random.randint(20, 300),
                                      "device": {"userAgent": UA}}})}}])
        self.answers += 1
        if self.stats:
            self.stats["answers"] = self.stats.get("answers", 0) + 1

    def _pick(self, n=4, idx=0):
        # answer_map is filled live from result messages — use it if available
        if idx in self.answer_map:
            return self.answer_map[idx]
        m = {"first":0,"second":1,"third":2,"fourth":3}
        return m.get(self.strategy, random.randint(0, n-1))

    def _parse(self, raw_c):
        try:
            return json.loads(raw_c) if isinstance(raw_c, str) else raw_c
        except Exception:
            return {}

    def on_message(self, ws, raw):
        try:
            msgs = json.loads(raw)
            if not isinstance(msgs, list):
                msgs = [msgs]
            for msg in msgs:
                ch = msg.get("channel", "")

                if ch == "/meta/handshake" and msg.get("successful"):
                    self.client_id = msg["clientId"]
                    self._meta_connect()
                    for sub in ["/service/player", "/service/controller", "/service/status"]:
                        self._subscribe(sub)
                    self._join()

                elif ch == "/service/player":
                    data    = msg.get("data", {})
                    content = self._parse(data.get("content", "{}"))
                    ctype   = content.get("type", "")

                    # Question starts → answer it
                    if ctype in ("gameBlockStart", "startQuestion"):
                        if not self.joined:
                            self.joined = True
                            if self.stats:
                                self.stats["joined"] = self.stats.get("joined", 0) + 1
                            self._log(f"{G}joined ✓{R}")
                        n     = content.get("numberOfChoices", 4)
                        idx   = content.get("questionIndex", 0)
                        pick  = self._pick(n, idx)
                        known = idx in self.answer_map
                        lo, hi = TIMING_RANGES.get(self.timing, (0.3, 1.5))
                        delay  = random.uniform(lo, hi)
                        time.sleep(delay)
                        self._answer(pick, idx)
                        icon  = CHOICE_ICONS[pick % 4]
                        tag   = f"{G}✓ correct{R}" if known else f"{Y}? random{R}"
                        self._log(f"Q{idx+1} → {icon} {tag}  {DIM}({delay:.1f}s){R}")
                        if known:
                            self.correct += 1

                    # Results arrive → capture correct answer for NEXT time
                    elif ctype in ("revealAnswer", "questionEnd", "gameBlockEnd"):
                        correct = content.get("correctAnswers", content.get("correctAnswer", None))
                        idx     = content.get("questionIndex", content.get("gameBlockIndex", None))
                        if idx is not None and correct is not None:
                            # correctAnswers can be a list or int
                            ans = correct[0] if isinstance(correct, list) else int(correct)
                            self.answer_map[idx] = ans
                            if self.stats and "answer_map" in self.stats:
                                self.stats["answer_map"][idx] = ans
                            self._log(f"{DIM}Q{idx+1} answer captured: {CHOICE_ICONS[ans % 4]}{R}")

                elif ch == "/service/controller":
                    data  = msg.get("data", {})
                    ctype = data.get("type", "")
                    if ctype == "loginResponse" and not self.joined:
                        content = self._parse(data.get("content", "{}"))
                        if "duplicate" not in str(content).lower():
                            self.joined = True
                            if self.stats:
                                self.stats["joined"] = self.stats.get("joined", 0) + 1
                            self._log(f"{G}joined ✓{R}")
        except Exception:
            pass

    def on_error(self, ws, err): self._log(f"{RD}✗ {err}{R}")
    def on_close(self, ws, *a):  self.running = False
    def on_open(self, ws):       self._handshake()

    def run(self):
        self.running = True
        headers = [
            f"Origin: https://kahoot.it",
            f"User-Agent: {UA}",
            f"Referer: https://kahoot.it/",
            f"Accept-Language: en-US,en;q=0.9",
            f"Cache-Control: no-cache",
            f"Pragma: no-cache",
        ]
        if self.cookies:
            headers.append(f"Cookie: {self.cookies}")
        safe_chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'
        tok_encoded = quote(self.token, safe=safe_chars)
        self.ws = websocket.WebSocketApp(
            f"{self.ws_base}/cometd/{self.session_id}/{tok_encoded}",
            header=headers,
            on_open=self.on_open, on_message=self.on_message,
            on_error=self.on_error, on_close=self.on_close)
        self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE,
                                     "check_hostname": False})

    def stop(self):
        self.running = False
        try: self.ws.close()
        except Exception: pass

# ── Helper ────────────────────────────────────────────────────────────────────
def rname(prefix=""):
    return (prefix + str(random.randint(100, 9999))) if prefix else \
           (random.choice(FUNNY_NAMES) + str(random.randint(10, 99)))

def divider(label=""):
    line = "─" * 40
    if label:
        pad = (40 - len(label) - 2) // 2
        print(f"\n  {DIM}{'─'*pad} {W}{B}{label}{R} {DIM}{'─'*pad}{R}")
    else:
        print(f"  {DIM}{line}{R}")

def prompt(msg):
    return input(f"  {Y}▸ {W}{msg}{R} ").strip()

def success(msg): print(f"  {G}✓ {msg}{R}")
def error(msg):   print(f"  {RD}✗ {msg}{R}"); sys.exit(1)
def info(msg):    print(f"  {BL}→ {msg}{R}")

# ── Modes ─────────────────────────────────────────────────────────────────────
def mode_auto(pin, token, session_id, answer_map=None, cookies="", ws_base="wss://kahoot.it"):
    answer_map = answer_map or {}
    divider("AUTO-ANSWER")
    name = prompt("Your player name:") or "KahootBot"

    # ── Strategy ──────────────────────────────────────────────────────────────
    print(f"\n  {W}{B}Antwort-Strategie:{R}")
    if answer_map:
        print(f"  {W}1{R}  {G}{B}Immer richtig ✓{R}  {DIM}(alle Fragen, UUID geladen){R}")
    else:
        print(f"  {W}1{R}  {DIM}Immer richtig  🔒 UUID erforderlich{R}")
    print(f"  {W}2{R}  {Y}Zufällig{R}  {DIM}· Q1 zufällig, ab Q2 automatisch richtig{R}\n")

    raw_s = prompt("Strategie [1/2]:")
    if raw_s == "1" and answer_map:
        strategy = "correct"
    else:
        strategy = "random"

    # ── Timing ────────────────────────────────────────────────────────────────
    print(f"\n  {W}{B}Antwort-Timing:{R}  {DIM}(damit es nicht auffällt){R}")
    print(f"  {W}1{R}  ⚡ Erster   {DIM}0–1.5 s{R}")
    print(f"  {W}2{R}  🕐 Mittlerer {DIM}3–7 s{R}")
    print(f"  {W}3{R}  🐢 Letzter   {DIM}9–16 s{R}\n")

    raw_t = prompt("Timing [1/2/3]:")
    timing = {"1": "fast", "2": "medium", "3": "slow"}.get(raw_t, "fast")

    # ── Summary ───────────────────────────────────────────────────────────────
    divider()
    strat_label  = f"{G}immer richtig ✓{R}" if strategy == "correct" else f"{Y}zufällig → ab Q2 richtig{R}"
    timing_label = {"fast": f"⚡ erster", "medium": "🕐 mittlerer", "slow": "🐢 letzter"}[timing]
    info(f"Joining als {M}{B}{name}{R}  ·  {strat_label}  ·  {BL}{timing_label} Antworter{R}")

    bot = KahootBot(name, token, session_id, pin,
                    strategy=strategy, timing=timing,
                    answer_map=answer_map, cookies=cookies, ws_base=ws_base)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
        print(f"\n  {Y}Stopped — answers: {bot.answers}  correct: {G}{bot.correct}{R}")


def mode_flood(pin, token, session_id, answer_map=None, cookies="", ws_base="wss://kahoot.it"):
    answer_map = answer_map or {}
    divider("FLOOD MODE")
    prefix = prompt("Name prefix (empty = random funny names):")
    try:    count = int(prompt("Bot count [default 30]:") or "30")
    except: count = 30
    count = min(max(count, 1), 200)
    strategy = "correct" if answer_map else "random"
    divider()
    label = f"{G}correct answers{R}" if strategy == "correct" else f"{Y}random{R}"
    info(f"Launching {Y}{count}{R} bots — strategy: {label}")

    stats   = {"joined": 0, "answers": 0}
    bots    = []
    threads = []
    stop_ev = threading.Event()

    def launch():
        for i in range(count):
            if stop_ev.is_set():
                break
            name = rname(prefix)
            # first bot is visible so errors are shown
            silent = (i > 0)
            bot  = KahootBot(name, token, session_id, pin,
                             strategy=strategy, silent=silent,
                             stats=stats, answer_map=answer_map, cookies=cookies, ws_base=ws_base)
            bots.append(bot)
            t = threading.Thread(target=bot.run, daemon=True)
            threads.append(t)
            t.start()
            launched = i + 1
            print(f"  {G}▶{R} launched {Y}{launched}/{count}{R}  "
                  f"joined: {G}{stats['joined']}{R}  "
                  f"answers: {BL}{stats['answers']}{R}",
                  end="\r", flush=True)
            time.sleep(0.35)

    lt = threading.Thread(target=launch, daemon=True)
    lt.start()

    print(f"\n  {DIM}Press ENTER to stop...{R}")
    try:
        input()
    except KeyboardInterrupt:
        pass

    stop_ev.set()
    print(f"\n  {RD}Stopping {len(bots)} bots...{R}")
    for b in bots:
        b.stop()
    print(f"  {G}Done — {stats['joined']} joined, {stats['answers']} answers given{R}")


def mode_spam(pin, token, session_id, cookies="", ws_base="wss://kahoot.it"):
    divider("NAME SPAM")
    prefix = prompt("Name prefix [default 'SPAM']:") or "SPAM"
    try:    count = int(prompt("How many [default 50]:") or "50")
    except: count = 50
    count = min(max(count, 1), 200)
    divider()
    info(f"Filling lobby with {Y}{count}{R} names — bots stay until you press ENTER\n")

    bots = []

    for i in range(count):
        name = prefix + str(random.randint(1000, 9999))
        bot  = KahootBot(name, token, session_id, pin,
                         silent=True, cookies=cookies, ws_base=ws_base)
        t = threading.Thread(target=bot.run, daemon=True)
        t.start()
        bots.append(bot)
        pct = int((i + 1) / count * 20)
        bar = G + "█" * pct + DIM + "░" * (20 - pct) + R
        print(f"  [{bar}] {i+1}/{count}  {DIM}{name}{R}",
              end="\r", flush=True)
        time.sleep(0.35)

    print(f"\n\n  {G}✓ {count} bots in lobby — press ENTER to disconnect{R}")
    try:
        input()
    except KeyboardInterrupt:
        pass

    for b in bots:
        b.stop()
    print(f"  {RD}All bots disconnected{R}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    clr()
    print(BANNER)

    pin = prompt("Enter Kahoot PIN:")
    if not pin.isdigit():
        error("PIN must be numbers only")

    print()
    info("Checking game...")
    token, session_id, ws_base, cookies, err = get_session(pin)
    if err:
        error(err)

    success(f"Game found!  Session: {DIM}{session_id}{R}")

    # Optional: load correct answers via quiz UUID
    answer_map = {}
    print(f"\n  {DIM}Kahoot quiz link or UUID for correct answers (enter to skip):{R}")
    quiz_input = prompt("Quiz link/UUID:").strip()
    if quiz_input:
        # Extract UUID from link like kahoot.it/kahoot/play?quizId=UUID or raw UUID
        uuid_m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", quiz_input, re.I)
        if uuid_m:
            info("Fetching answers...")
            answer_map = fetch_answers(uuid_m.group(0))
            if answer_map:
                success(f"Loaded {G}{len(answer_map)}{R} correct answers!")
            else:
                print(f"  {Y}⚠ Quiz is private or not found{R}")
        else:
            print(f"  {Y}⚠ No UUID found in input{R}")

    divider("SELECT MODE")
    print(f"\n  {W}{B}1{R}  ⚡ Auto-Answer  {DIM}join & answer every question{R}")
    print(f"  {W}{B}2{R}  👥 Flood        {DIM}fill lobby with bots (max 200){R}")
    print(f"  {W}{B}3{R}  💣 Name Spam    {DIM}overflow lobby with random names{R}\n")

    choice = prompt("Mode [1/2/3]:")

    if choice == "1":
        mode_auto(pin, token, session_id, answer_map, cookies, ws_base)
    elif choice == "2":
        mode_flood(pin, token, session_id, answer_map, cookies, ws_base)
    elif choice == "3":
        mode_spam(pin, token, session_id, cookies, ws_base)
    else:
        error("Invalid choice")

if __name__ == "__main__":
    main()
