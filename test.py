#!/usr/bin/env python3
"""Quick connection test — run: python3 test.py <PIN>"""
import sys, time, json, base64, ssl, socket, threading, requests, websocket
requests.packages.urllib3.disable_warnings()
websocket.enableTrace(False)

# iSH fix
_orig = socket.socket.setsockopt
def _safe(self, *a, **kw):
    try: return _orig(self, *a, **kw)
    except OSError: pass
socket.socket.setsockopt = _safe

from kahoot import get_session, solve_challenge, xor_decode

PIN = sys.argv[1] if len(sys.argv) > 1 else input("PIN: ").strip()

print(f"\n[1] Fetching session for PIN {PIN}...")
token, session_id, ws_base, cookies, err = get_session(PIN)
if err:
    print(f"    FAIL: {err}")
    sys.exit(1)
print(f"    token   : {token[:20]}...")
print(f"    session : {session_id}")
print(f"    ws_base : {ws_base}")

WS_URL = f"{ws_base}/cometd/{session_id}/{token}"
print(f"\n[2] Testing WebSocket: {WS_URL[:60]}...")

result = {"connected": False, "handshake": False, "joined": False, "error": None}
done   = threading.Event()

def on_open(ws):
    result["connected"] = True
    print("    connected ✓")
    ws.send(json.dumps([{
        "channel": "/meta/handshake", "version": "1.0",
        "minimumVersion": "1.0",
        "supportedConnectionTypes": ["websocket"],
        "id": "1",
        "ext": {"ack": True, "timesync": {"tc": int(time.time()*1000), "l": 0, "o": 0}}
    }]))

def on_message(ws, raw):
    msgs = json.loads(raw)
    if not isinstance(msgs, list): msgs = [msgs]
    for m in msgs:
        ch = m.get("channel", "")
        ok = m.get("successful", None)
        if ch == "/meta/handshake":
            if ok:
                result["handshake"] = True
                print(f"    handshake ✓  clientId={m.get('clientId','?')[:12]}...")
                ws.send(json.dumps([{
                    "channel": "/service/controller",
                    "clientId": m["clientId"], "id": "2",
                    "data": {"type": "login", "gameid": session_id,
                             "host": "kahoot.it", "name": "TestBot_DELETE_ME",
                             "content": json.dumps({"device": {"userAgent": "Mozilla/5.0", "screen": {"width": 390, "height": 844}}})}}]))
            else:
                result["error"] = f"handshake failed: {m}"
                print(f"    handshake FAIL: {m.get('error', m)}")
                done.set(); ws.close()
        elif ch == "/service/controller":
            ctype = m.get("data", {}).get("type", "")
            if ctype == "loginResponse":
                result["joined"] = True
                print("    login ✓  bot joined lobby!")
                done.set(); ws.close()

def on_error(ws, e):
    result["error"] = str(e)
    print(f"    ERROR: {e}")
    done.set()

def on_close(ws, *a):
    done.set()

headers = [
    "Origin: https://kahoot.it",
    "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X)",
    "Referer: https://kahoot.it/",
    "Accept-Language: en-US,en;q=0.9",
    "Cache-Control: no-cache",
]
if cookies:
    headers.append(f"Cookie: {cookies}")

ws = websocket.WebSocketApp(WS_URL, header=headers,
    on_open=on_open, on_message=on_message,
    on_error=on_error, on_close=on_close)

t = threading.Thread(target=lambda: ws.run_forever(
    sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False}), daemon=True)
t.start()
done.wait(timeout=12)
ws.close()

print(f"\n{'='*40}")
print(f"  connected : {'✓' if result['connected'] else '✗'}")
print(f"  handshake : {'✓' if result['handshake'] else '✗'}")
print(f"  joined    : {'✓' if result['joined'] else '✗'}")
if result["error"]:
    print(f"  error     : {result['error']}")
print('='*40)

if result["joined"]:
    print("\n  ALL GOOD — main tool should work!")
elif result["handshake"]:
    print("\n  Token OK but login failed — check gameid/name logic")
elif result["connected"]:
    print("\n  Connected but handshake failed — token might be wrong")
else:
    print("\n  Could not connect — check WS URL / network")
