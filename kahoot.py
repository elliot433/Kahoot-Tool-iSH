#!/usr/bin/env python3
"""
KahootKit - Auto-Answer, Flooder & Spammer for iSH
"""

import os
import re
import sys
import json
import time
import random
import string
import threading
import subprocess
import requests
import websocket

requests.packages.urllib3.disable_warnings()

KAHOOT_SESSION  = "https://kahoot.it/reserve/session/{}/?{}"
KAHOOT_WS       = "wss://kahoot.it/cometd/{}/{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Accept": "application/json",
    "Origin": "https://kahoot.it",
}

COLORS = {
    "g": "\033[92m", "r": "\033[91m", "y": "\033[93m",
    "b": "\033[94m", "m": "\033[95m", "c": "\033[96m",
    "w": "\033[97m", "dim": "\033[2m", "rst": "\033[0m",
    "bold": "\033[1m",
}

def c(color, text):
    return COLORS.get(color, "") + str(text) + COLORS["rst"]

BANNER = f"""
{COLORS['g']}╔═══════════════════════════════╗
║       K A H O O T K I T       ║
║   Auto-Answer · Flood · Spam  ║
╚═══════════════════════════════╝{COLORS['rst']}
"""


# ── Challenge Solver ─────────────────────────────────────────────────────────

def solve_challenge(js: str) -> str:
    # Try node.js first (most reliable)
    try:
        result = subprocess.run(
            ["node", "-e", js + "\nconsole.log(typeof challenge === 'function' ? challenge() : '');"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    # Fallback: Python regex parser for known Kahoot challenge patterns
    try:
        # Extract encoded key
        key_m = re.search(r'decode\.call\(this,\s*["\']([^"\']+)["\']', js)
        if not key_m:
            key_m = re.search(r'"([A-Za-z0-9+/=]{20,})"', js)
        if not key_m:
            return ""
        key = key_m.group(1)

        # Extract the transform expression (function body)
        func_m = re.search(r'function\s*\(\s*\w\s*\)\s*\{[^}]*return\s+(.+?)[\s;]*\}', js)
        if not func_m:
            return ""
        expr_template = func_m.group(1).strip()

        # Find any variable defined before (like offset = 70)
        var_vals = {}
        for vm in re.finditer(r'var\s+(\w+)\s*=\s*(\d+)', js):
            var_vals[vm.group(1)] = int(vm.group(2))

        result = ""
        for ch in key:
            x = ord(ch)
            expr = expr_template
            # Replace var names with their values
            for var, val in var_vals.items():
                expr = re.sub(r'\b' + var + r'\b', str(val), expr)
            # Replace the parameter (x, y, or whatever)
            param_m = re.search(r'function\s*\((\w+)\)', js)
            param = param_m.group(1) if param_m else "x"
            expr = re.sub(r'\b' + param + r'\b', str(x), expr)
            try:
                val = eval(expr)
                result += chr(int(val) % 256)
            except Exception:
                result += ch
        return result
    except Exception:
        return ""


def xor_decode(token: str, key: str) -> str:
    if not key:
        return token
    result = ""
    for i, ch in enumerate(token):
        result += chr(ord(ch) ^ ord(key[i % len(key)]))
    return result


# ── Session ───────────────────────────────────────────────────────────────────

def get_session(pin: str):
    ts = int(time.time() * 1000)
    try:
        r = requests.get(KAHOOT_SESSION.format(pin, ts), headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return None, None, "Game not found or not started yet"
        if r.status_code != 200:
            return None, None, f"HTTP {r.status_code}"

        raw_token = r.headers.get("x-authtoken", "")
        data = r.json()
        challenge_js = data.get("challenge", "")

        if challenge_js:
            key = solve_challenge(challenge_js)
            token = xor_decode(raw_token, key)
        else:
            token = raw_token

        session_id = data.get("sessionId", pin)
        return token, session_id, None
    except Exception as e:
        return None, None, str(e)


# ── Bot ───────────────────────────────────────────────────────────────────────

class KahootBot:
    def __init__(self, name: str, token: str, session_id: str, pin: str,
                 strategy: str = "random", silent: bool = False):
        self.name = name
        self.token = token
        self.session_id = str(session_id)
        self.pin = str(pin)
        self.strategy = strategy
        self.silent = silent
        self.ws = None
        self.client_id = None
        self.msg_id = 0
        self.joined = False
        self.score = 0
        self.running = False

    def _log(self, msg):
        if not self.silent:
            print(f"  {c('dim', self.name[:12].ljust(12))} {msg}")

    def _next_id(self):
        self.msg_id += 1
        return str(self.msg_id)

    def _send(self, payload):
        try:
            self.ws.send(json.dumps(payload))
        except Exception:
            pass

    def _handshake(self):
        self._send([{
            "channel": "/meta/handshake",
            "version": "1.0",
            "minimumVersion": "1.0",
            "supportedConnectionTypes": ["websocket"],
            "id": self._next_id(),
            "ext": {"ack": True, "timesync": {"tc": int(time.time() * 1000), "l": 0, "o": 0}},
        }])

    def _connect(self):
        self._send([{
            "channel": "/meta/connect",
            "clientId": self.client_id,
            "connectionType": "websocket",
            "id": self._next_id(),
            "ext": {"ack": 0, "timesync": {"tc": int(time.time() * 1000), "l": 0, "o": 0}},
        }])

    def _subscribe(self, channel):
        self._send([{
            "channel": "/meta/subscribe",
            "clientId": self.client_id,
            "subscription": channel,
            "id": self._next_id(),
        }])

    def _join(self):
        self._send([{
            "channel": "/service/controller",
            "clientId": self.client_id,
            "id": self._next_id(),
            "data": {
                "type": "login",
                "gameid": self.session_id,
                "host": "kahoot.it",
                "name": self.name,
                "content": json.dumps({"device": {"userAgent": HEADERS["User-Agent"], "screen": {"width": 390, "height": 844}}}),
            },
        }])

    def _answer(self, choice: int, question_idx: int):
        self._send([{
            "channel": "/service/controller",
            "clientId": self.client_id,
            "id": self._next_id(),
            "data": {
                "gameid": self.session_id,
                "host": "kahoot.it",
                "type": "message",
                "id": 6,
                "content": json.dumps({
                    "choice": choice,
                    "meta": {"lag": random.randint(20, 200), "device": {"userAgent": HEADERS["User-Agent"]}},
                }),
            },
        }])

    def _pick_answer(self, num_choices: int = 4) -> int:
        if self.strategy == "first":
            return 0
        if self.strategy == "second":
            return 1
        if self.strategy == "third":
            return 2
        if self.strategy == "fourth":
            return 3
        return random.randint(0, num_choices - 1)

    def on_message(self, ws, raw):
        try:
            messages = json.loads(raw)
            if not isinstance(messages, list):
                messages = [messages]
            for msg in messages:
                ch = msg.get("channel", "")

                if ch == "/meta/handshake":
                    if msg.get("successful"):
                        self.client_id = msg["clientId"]
                        self._connect()
                        self._subscribe("/service/player")
                        self._subscribe("/service/controller")
                        self._subscribe("/service/status")
                        self._join()

                elif ch == "/service/player":
                    data = msg.get("data", {})
                    content_raw = data.get("content", "{}")
                    try:
                        content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
                    except Exception:
                        content = {}
                    typ = data.get("type", "")
                    game_block = content.get("gameBlockIndex", None)

                    # Question start
                    if typ == "message" and game_block is not None and not self.joined:
                        self.joined = True
                        self._log(c("g", "joined ✓"))

                    if "numberOfChoices" in content or "questionIndex" in content:
                        num_choices = content.get("numberOfChoices", 4)
                        idx = content.get("questionIndex", game_block or 0)
                        delay = random.uniform(0.3, 2.5)
                        choice = self._pick_answer(num_choices)
                        time.sleep(delay)
                        self._answer(choice, idx)
                        labels = ["🔴", "🔵", "🟡", "🟢"]
                        self._log(f"Q{idx+1} → {labels[choice % 4]} ({delay:.1f}s)")

                    # Score update
                    if "totalScore" in content:
                        self.score = content["totalScore"]

                elif ch == "/service/controller":
                    data = msg.get("data", {})
                    if data.get("type") == "loginResponse":
                        ctype = data.get("content", "")
                        if isinstance(ctype, str) and "duplicate" in ctype.lower():
                            self._log(c("y", "duplicate name, reconnecting..."))
                        elif not self.joined:
                            self.joined = True
                            self._log(c("g", "joined ✓"))

        except Exception:
            pass

    def on_error(self, ws, err):
        self._log(c("r", f"error: {err}"))

    def on_close(self, ws, *args):
        self.running = False

    def on_open(self, ws):
        self._handshake()

    def run(self):
        url = KAHOOT_WS.format(self.session_id, self.token)
        self.running = True
        self.ws = websocket.WebSocketApp(
            url,
            header={"Origin": "https://kahoot.it"},
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self.ws.run_forever(ping_interval=30, ping_timeout=10)

    def stop(self):
        self.running = False
        if self.ws:
            self.ws.close()


# ── Name Generators ───────────────────────────────────────────────────────────

FUNNY_NAMES = [
    "YourMomBot", "WifiPassword", "FBI_Agent", "Area51Guard",
    "IAmAHuman", "TotallyNotABot", "PleaseIgnore", "NotCheating",
    "KahootKing", "AnswerBot9000", "1337Hax0r", "SchoolComputer",
    "SlayQueen", "BrainRot420", "NPC_Player", "TheChosenOne",
    "IForgotMyName", "HelloWorld", "NULL", "undefined",
    "DROP_TABLE", "XSS_Alert", "AdminAdmin", "GodMode",
]

def random_name(prefix: str = "") -> str:
    if prefix:
        return prefix + str(random.randint(100, 9999))
    return random.choice(FUNNY_NAMES) + str(random.randint(10, 99))


# ── Modes ─────────────────────────────────────────────────────────────────────

def mode_single(pin: str):
    print(c("b", "\n[Auto-Answer Mode]"))
    name = input(c("y", "  Your name: ")).strip() or "KahootBot"
    print("  Strategy:")
    print("  1) Random answer")
    print("  2) Always first (red)")
    print("  3) Always second (blue)")
    strat_map = {"1": "random", "2": "first", "3": "second"}
    strat = strat_map.get(input(c("y", "  Choice [1/2/3]: ")).strip(), "random")

    print(c("c", f"\n  Connecting to game {pin}..."))
    token, session_id, err = get_session(pin)
    if err:
        print(c("r", f"  Error: {err}"))
        return

    print(c("g", f"  Session OK — joining as '{name}'"))
    bot = KahootBot(name, token, session_id, pin, strategy=strat, silent=False)
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
        print(c("y", "\n  Stopped."))


def mode_flood(pin: str):
    print(c("b", "\n[Flood Mode]"))
    prefix = input(c("y", "  Name prefix (empty = funny names): ")).strip()
    try:
        count = int(input(c("y", "  Bot count [default 20]: ")).strip() or "20")
    except ValueError:
        count = 20
    count = min(count, 200)

    print(c("c", f"\n  Getting session for game {pin}..."))
    token, session_id, err = get_session(pin)
    if err:
        print(c("r", f"  Error: {err}"))
        return

    print(c("g", f"  Session OK — launching {count} bots...\n"))
    bots = []
    threads = []

    for i in range(count):
        name = random_name(prefix)
        bot = KahootBot(name, token, session_id, pin, strategy="random", silent=(count > 10))
        bots.append(bot)
        t = threading.Thread(target=bot.run, daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.15)
        if not bot.silent:
            print(f"  {c('g', '▶')} {name}")

    if count > 10:
        print(c("g", f"  {count} bots launched silently"))

    print(c("y", "\n  Press ENTER to stop all bots..."))
    try:
        input()
    except KeyboardInterrupt:
        pass

    print(c("r", "  Stopping all bots..."))
    for bot in bots:
        bot.stop()
    print(c("g", "  Done."))


def mode_spam(pin: str):
    print(c("b", "\n[Name Spam Mode]"))
    print("  Spam-joins and immediately leaves to fill the lobby.\n")
    prefix = input(c("y", "  Name prefix [default 'SPAM']: ")).strip() or "SPAM"
    try:
        count = int(input(c("y", "  How many joins [default 50]: ")).strip() or "50")
    except ValueError:
        count = 50

    print(c("c", f"\n  Getting session..."))
    token, session_id, err = get_session(pin)
    if err:
        print(c("r", f"  Error: {err}"))
        return

    print(c("g", f"  Spamming {count} names into lobby...\n"))
    joined = 0

    def spam_one(name):
        nonlocal joined
        try:
            bot = KahootBot(name, token, session_id, pin, silent=True)
            t = threading.Thread(target=bot.run, daemon=True)
            t.start()
            time.sleep(1.5)
            bot.stop()
            joined += 1
        except Exception:
            pass

    threads = []
    for i in range(count):
        name = prefix + str(random.randint(1000, 9999))
        t = threading.Thread(target=spam_one, args=(name,), daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.2)
        print(f"  {c('y', '→')} {name}  ({joined} joined)", end="\r")

    for t in threads:
        t.join(timeout=5)

    print(c("g", f"\n  Done — {joined}/{count} joins completed."))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(BANNER)

    pin = input(c("g", "  Enter Kahoot PIN: ")).strip()
    if not pin.isdigit():
        print(c("r", "  Invalid PIN."))
        sys.exit(1)

    print(c("c", f"\n  Checking game {pin}..."))
    token, session_id, err = get_session(pin)
    if err:
        print(c("r", f"  Game not found: {err}"))
        sys.exit(1)
    print(c("g", f"  Game found! Session: {session_id}\n"))

    print(f"  {c('w', '1)')} Auto-Answer  {c('dim', '— join and answer questions automatically')}")
    print(f"  {c('w', '2)')} Flood        {c('dim', '— fill lobby with bots')}")
    print(f"  {c('w', '3)')} Name Spam    {c('dim', '— spam lobby with names')}")

    choice = input(c("y", "\n  Mode [1/2/3]: ")).strip()

    if choice == "1":
        mode_single(pin)
    elif choice == "2":
        mode_flood(pin)
    elif choice == "3":
        mode_spam(pin)
    else:
        print(c("r", "  Invalid choice."))


if __name__ == "__main__":
    main()
