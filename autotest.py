#!/usr/bin/env python3
"""
Autonomous test for all KahootKit modes.
Usage: python3 autotest.py <PIN>
Tests: session fetch, auto-answer join, flood (3 bots), spam (3 bots)
"""
import sys, time, json, threading, ssl, socket, base64, random
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import requests, websocket
from urllib.parse import quote

requests.packages.urllib3.disable_warnings()
websocket.enableTrace(False)

_orig = socket.socket.setsockopt
def _safe(self, *a, **kw):
    try: return _orig(self, *a, **kw)
    except OSError: pass
socket.socket.setsockopt = _safe

from kahoot import get_session, KahootBot, TIMING_RANGES

PIN = sys.argv[1] if len(sys.argv) > 1 else input("PIN: ").strip()

PASS = "\033[92mOK\033[0m"
FAIL = "\033[91mFAIL\033[0m"
WARN = "\033[93mWARN\033[0m"

results = {}

# ─── 1. Session fetch ──────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"[1] SESSION FETCH  (PIN={PIN})")
print(f"{'='*50}")
token, session_id, ws_base, cookies, err = get_session(PIN)
if err or not token:
    print(f"    {FAIL} get_session failed: {err}")
    sys.exit(1)

print(f"    {PASS} token      : {token[:20]}...")
print(f"    {PASS} session_id : {session_id}")
print(f"    {PASS} ws_base    : {ws_base}")
results["session"] = True


# ─── helper: run bot, wait for joined, return True/False ──────────────────────
def test_bot_joins(name, timeout=15):
    joined_ev = threading.Event()
    error_ev  = threading.Event()

    class TestBot(KahootBot):
        def on_message(self, ws, raw):
            super().on_message(ws, raw)
            if self.joined:
                joined_ev.set()
                ws.close()
        def on_error(self, ws, e):
            error_ev.set()
            joined_ev.set()  # unblock wait

    bot = TestBot(name, token, session_id, PIN,
                  strategy="random", timing="fast",
                  silent=False, cookies=cookies, ws_base=ws_base)
    t = threading.Thread(target=bot.run, daemon=True)
    t.start()
    joined_ev.wait(timeout=timeout)
    bot.stop()
    return bot.joined


# ─── 2. Auto-Answer: single bot join ──────────────────────────────────────────
print(f"\n{'='*50}")
print(f"[2] AUTO-ANSWER — single bot join")
print(f"{'='*50}")
joined = test_bot_joins("TestAutoBot")
tag = PASS if joined else FAIL
print(f"    {tag} bot joined : {joined}")
results["auto_answer"] = joined


# ─── 3. Flood: 3 bots ─────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"[3] FLOOD — 3 bots")
print(f"{'='*50}")
flood_stats = {"joined": 0}
flood_bots = []
flood_done = threading.Event()

def run_flood_bot(name):
    class FloodBot(KahootBot):
        def on_message(self, ws, raw):
            super().on_message(ws, raw)
            if self.joined:
                flood_stats["joined"] = flood_stats["joined"] + 1

    bot = FloodBot(name, token, session_id, PIN,
                   strategy="random", timing="fast",
                   silent=True, cookies=cookies, ws_base=ws_base)
    flood_bots.append(bot)
    bot.run()

threads = []
for i in range(3):
    nm = f"FloodTest{i+1}"
    t = threading.Thread(target=run_flood_bot, args=(nm,), daemon=True)
    threads.append(t)
    t.start()
    time.sleep(0.5)

time.sleep(10)
for b in flood_bots:
    try: b.stop()
    except: pass

j = flood_stats["joined"]
tag = PASS if j >= 2 else WARN
print(f"    {tag} joined {j}/3 flood bots")
results["flood"] = j >= 2


# ─── 4. Spam: 3 bots join and stay ───────────────────────────────────────────
print(f"\n{'='*50}")
print(f"[4] SPAM — 3 name-spam bots")
print(f"{'='*50}")
spam_stats = {"joined": 0}
spam_bots = []

def run_spam_bot(name):
    class SpamBot(KahootBot):
        def on_message(self, ws, raw):
            super().on_message(ws, raw)
            if self.joined:
                spam_stats["joined"] = spam_stats["joined"] + 1

    bot = SpamBot(name, token, session_id, PIN,
                  silent=True, cookies=cookies, ws_base=ws_base)
    spam_bots.append(bot)
    bot.run()

for i in range(3):
    nm = f"SPAM{random.randint(1000,9999)}"
    t = threading.Thread(target=run_spam_bot, args=(nm,), daemon=True)
    t.start()
    time.sleep(0.5)

time.sleep(10)
for b in spam_bots:
    try: b.stop()
    except: pass

j = spam_stats["joined"]
tag = PASS if j >= 2 else WARN
print(f"    {tag} joined {j}/3 spam bots")
results["spam"] = j >= 2


# ─── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print("  RESULTS")
print(f"{'='*50}")
for k, v in results.items():
    tag = PASS if v else FAIL
    print(f"  {tag}  {k}")
print(f"{'='*50}\n")

all_ok = all(results.values())
sys.exit(0 if all_ok else 1)
