#!/usr/bin/env python3
import os, re, sys, json, time, random, threading, subprocess, requests, websocket
requests.packages.urllib3.disable_warnings()

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
def solve_challenge(js: str) -> str:
    # Primary: let Node.js run the actual JS
    try:
        wrap = js + """
var res = "";
if (typeof challenge === "function") { res = challenge(); }
else if (typeof challenge === "string") { res = challenge; }
process.stdout.write(String(res));
"""
        out = subprocess.run(["node", "-e", wrap],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout:
            return out.stdout
    except Exception:
        pass

    # Fallback: Python regex solver for known Kahoot patterns
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
def get_session(pin: str):
    ts = int(time.time() * 1000)
    try:
        r = requests.get(SESSION_URL.format(pin, ts), headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return None, None, None, "Game not found or not started"
        if r.status_code != 200:
            return None, None, None, f"HTTP {r.status_code}"
        raw_token = r.headers.get("x-authtoken", "")
        data = r.json()
        challenge_js = data.get("challenge", "")
        token = xor_decode(raw_token, solve_challenge(challenge_js)) if challenge_js else raw_token
        kahoot_id = data.get("kahootId", data.get("quizId", ""))
        return token, str(data.get("sessionId", pin)), kahoot_id, None
    except Exception as e:
        return None, None, None, str(e)

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
class KahootBot:
    def __init__(self, name, token, session_id, pin,
                 strategy="random", silent=False, stats=None, answer_map=None):
        self.name       = name
        self.token      = token
        self.session_id = session_id
        self.pin        = pin
        self.strategy   = strategy
        self.silent     = silent
        self.stats      = stats       # shared dict for live stats
        self.answer_map = answer_map or {}  # {question_index: correct_choice}
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
        if self.strategy == "correct" and idx in self.answer_map:
            return self.answer_map[idx]
        m = {"first":0,"second":1,"third":2,"fourth":3}
        return m.get(self.strategy, random.randint(0, n-1))

    def on_message(self, ws, raw):
        try:
            for msg in (json.loads(raw) if isinstance(json.loads(raw), list) else [json.loads(raw)]):
                ch = msg.get("channel", "")

                if ch == "/meta/handshake" and msg.get("successful"):
                    self.client_id = msg["clientId"]
                    self._meta_connect()
                    for sub in ["/service/player", "/service/controller", "/service/status"]:
                        self._subscribe(sub)
                    self._join()

                elif ch == "/service/player":
                    data    = msg.get("data", {})
                    raw_c   = data.get("content", "{}")
                    content = json.loads(raw_c) if isinstance(raw_c, str) else raw_c
                    ctype   = content.get("type", "")

                    if ctype in ("gameBlockStart", "startQuestion"):
                        if not self.joined:
                            self.joined = True
                            if self.stats:
                                self.stats["joined"] = self.stats.get("joined", 0) + 1
                            self._log(f"{G}joined ✓{R}")
                        n     = content.get("numberOfChoices", 4)
                        idx   = content.get("questionIndex", 0)
                        pick  = self._pick(n, idx)
                        is_correct = idx in self.answer_map and self.answer_map[idx] == pick
                        if is_correct:
                            self.correct += 1
                        delay = random.uniform(0.4, 2.5)
                        time.sleep(delay)
                        self._answer(pick, idx)
                        icon  = CHOICE_ICONS[pick % 4]
                        check = f"{G}✓{R}" if is_correct else f"{DIM}?{R}"
                        self._log(f"Q{idx+1} → {icon} {check}  {DIM}({delay:.1f}s){R}")

                elif ch == "/service/controller":
                    data  = msg.get("data", {})
                    ctype = data.get("type", "")
                    if ctype == "loginResponse" and not self.joined:
                        raw_c   = data.get("content", "{}")
                        content = json.loads(raw_c) if isinstance(raw_c, str) else raw_c
                        if not str(content).lower().count("duplicate"):
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
        self.ws = websocket.WebSocketApp(
            WS_URL.format(self.session_id, self.token),
            header={"Origin": "https://kahoot.it"},
            on_open=self.on_open, on_message=self.on_message,
            on_error=self.on_error, on_close=self.on_close)
        self.ws.run_forever(ping_interval=25)

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
def mode_auto(pin, token, session_id, answer_map=None):
    answer_map = answer_map or {}
    divider("AUTO-ANSWER")
    name = prompt("Your player name:") or "KahootBot"

    has_answers = bool(answer_map)
    print(f"\n  Strategy:")
    if has_answers:
        print(f"  {W}1{R} {G}{B}Always correct ✓{R}  {DIM}(answers loaded){R}")
    else:
        print(f"  {W}1{R} {DIM}Always correct  (unavailable — private quiz){R}")
    print(f"  {W}2{R} Random answer")
    print(f"  {W}3{R} Always 🔴 first")
    print(f"  {W}4{R} Always 🔵 second\n")

    raw = prompt("Choice [1-4]:")
    if raw == "1" and has_answers:
        strategy = "correct"
    elif raw == "3":
        strategy = "first"
    elif raw == "4":
        strategy = "second"
    else:
        strategy = "random"

    divider()
    label = f"{G}correct answers{R}" if strategy == "correct" else f"{Y}{strategy}{R}"
    info(f"Joining as {M}{B}{name}{R} — {label}")
    bot = KahootBot(name, token, session_id, pin,
                    strategy=strategy, answer_map=answer_map)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
        print(f"\n  {Y}Stopped — answers: {bot.answers}  correct: {G}{bot.correct}{R}")


def mode_flood(pin, token, session_id, answer_map=None):
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
            bot  = KahootBot(name, token, session_id, pin,
                             strategy=strategy, silent=(count > 8),
                             stats=stats, answer_map=answer_map)
            bots.append(bot)
            t = threading.Thread(target=bot.run, daemon=True)
            threads.append(t)
            t.start()
            if count <= 8:
                info(f"Launched {M}{name}{R}")
            else:
                print(f"  {G}▶{R} {DIM}{name.ljust(20)}{R} "
                      f"joined: {G}{stats['joined']}{R}   "
                      f"answers: {Y}{stats['answers']}{R}",
                      end="\r", flush=True)
            time.sleep(0.2)

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


def mode_spam(pin, token, session_id):
    divider("NAME SPAM")
    prefix = prompt("Name prefix [default 'SPAM']:") or "SPAM"
    try:    count = int(prompt("How many [default 50]:") or "50")
    except: count = 50
    divider()
    info(f"Spamming {Y}{count}{R} names into lobby...\n")

    done = [0]
    lock = threading.Lock()

    def spam_one():
        name = prefix + str(random.randint(1000, 9999))
        bot  = KahootBot(name, token, session_id, pin, silent=True)
        t = threading.Thread(target=bot.run, daemon=True)
        t.start()
        time.sleep(1.8)
        bot.stop()
        with lock:
            done[0] += 1
        pct = int(done[0] / count * 20)
        bar = G + "█" * pct + DIM + "░" * (20 - pct) + R
        print(f"  [{bar}] {done[0]}/{count}  {DIM}{name}{R}",
              end="\r", flush=True)

    threads = []
    for _ in range(count):
        t = threading.Thread(target=spam_one, daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.25)

    for t in threads:
        t.join(timeout=8)
    print(f"\n  {G}Done — {done[0]}/{count} names sent{R}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    clr()
    print(BANNER)

    pin = prompt("Enter Kahoot PIN:")
    if not pin.isdigit():
        error("PIN must be numbers only")

    print()
    info("Checking game...")
    token, session_id, kahoot_id, err = get_session(pin)
    if err:
        error(err)

    success(f"Game found!  Session: {DIM}{session_id}{R}")

    # Try to load correct answers
    answer_map = {}
    if kahoot_id:
        info("Fetching quiz answers...")
        answer_map = fetch_answers(kahoot_id)
        if answer_map:
            success(f"Loaded {G}{len(answer_map)}{R} correct answers!")
        else:
            print(f"  {Y}⚠ Answers not available (private quiz){R}")
    else:
        print(f"  {Y}⚠ Quiz ID not found — answers unavailable{R}")

    divider("SELECT MODE")
    print(f"\n  {W}{B}1{R}  ⚡ Auto-Answer  {DIM}join & answer every question{R}")
    print(f"  {W}{B}2{R}  👥 Flood        {DIM}fill lobby with bots (max 200){R}")
    print(f"  {W}{B}3{R}  💣 Name Spam    {DIM}overflow lobby with random names{R}\n")

    choice = prompt("Mode [1/2/3]:")

    if choice == "1":
        mode_auto(pin, token, session_id, answer_map)
    elif choice == "2":
        mode_flood(pin, token, session_id, answer_map)
    elif choice == "3":
        mode_spam(pin, token, session_id)
    else:
        error("Invalid choice")

if __name__ == "__main__":
    main()
