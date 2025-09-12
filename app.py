# app.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import random
import threading
import queue
import requests
from datetime import datetime
from flask import Flask, render_template_string, request, Response, jsonify

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

worker_thread = None
worker_stop = threading.Event()
log_queue = queue.Queue(maxsize=500)
worker_state = {"running": False, "accounts": [], "sessions": {}, "cooldowns": {}, "current_user": None, "stats": {}}

def push_log(text):
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        log_queue.put_nowait(f"[{ts}] {text}")
    except queue.Full:
        try:
            _ = log_queue.get_nowait()
            log_queue.put_nowait(f"[{ts}] {text}")
        except Exception:
            pass

def stream_logs():
    while worker_state["running"] or not log_queue.empty():
        try:
            line = log_queue.get(timeout=1.0)
            yield f"data: {line}\n\n"
        except queue.Empty:
            yield "data: \n\n"
    yield "data: [INFO] Worker stopped\n\n"

def insta_login(username, password):
    session = requests.Session()
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 10)", "X-IG-App-ID": "936619743392459"}
    login_url = "https://www.instagram.com/accounts/login/ajax/"
    session.headers.update(headers)
    try:
        r = session.get("https://www.instagram.com/accounts/login/", timeout=15)
        csrf = r.cookies.get("csrftoken", "")
        if csrf:
            session.headers.update({"X-CSRFToken": csrf})
        payload = {"username": username, "enc_password": f"#PWD_INSTAGRAM_BROWSER:0:{int(time.time())}:{password}"}
        res = session.post(login_url, data=payload, allow_redirects=True, timeout=15)
        if "authenticated" in res.text:
            return session
    except Exception as e:
        push_log(f"[ERROR] login {username}: {e}")
    return None

def change_group_name(thread_id, new_name, session):
    url = f"https://i.instagram.com/api/v1/direct_v2/threads/{thread_id}/update_title/"
    data = {"title": new_name}
    headers = {"User-Agent": "Instagram 155.0.0.37.107 Android", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    try:
        r = session.post(url, data=data, headers=headers, timeout=15)
        if r.status_code == 200:
            return True, "OK"
        else:
            return False, f"HTTP {r.status_code}: {r.text}"
    except Exception as e:
        return False, f"Request Error: {e}"

def safe_change_group_name(thread_id, new_name, session, delay):
    ok, resp = change_group_name(thread_id, new_name, session)
    ts = datetime.now().strftime("%H:%M:%S")
    if ok:
        return True, f"[{ts}] [{thread_id}] -> {new_name}", delay, False
    else:
        if "feedback_required" in resp or "is_spam" in resp or "429" in resp:
            return False, f"[{ts}] [{thread_id}] -> {new_name} | SPAM/BLOCK DETECTED", delay, True
        return False, f"[{ts}] [{thread_id}] -> {new_name} | {resp}", delay, False

def worker_loop(config):
    push_log("[INFO] Worker started")
    worker_state["running"] = True
    worker_state["stats"] = {"changes": 0, "errors": 0}
    accounts = []
    for u, p in config["accounts"]:
        push_log(f"[INFO] Logging in {u} ...")
        s = insta_login(u, p)
        if s:
            accounts.append({"username": u, "session": s, "rest_until": 0})
            push_log(f"[SUCCESS] {u} logged in")
        else:
            push_log(f"[ERROR] Login failed: {u}")
    if not accounts:
        push_log("[ERROR] No accounts logged in. Stopping worker.")
        worker_state["running"] = False
        return

    user_count = len(accounts)
    acc_index = 0
    thread_ids = config["thread_ids"]
    names = config["names"]
    delay = config["delay"]
    changes_per_account = config["changes_per_account"]
    rest_min = config["rest_min"]
    rest_max = config["rest_max"]
    suffix_random = config.get("suffix_random", True)

    push_log(f"[INFO] Starting main loop: {len(accounts)} accounts, {len(thread_ids)} threads, {len(names)} names")
    try:
        while not worker_stop.is_set():
            acc = accounts[acc_index % user_count]
            acc_index += 1
            now = time.time()
            if acc["rest_until"] > now:
                left = int(acc["rest_until"] - now)
                m, s_rem = divmod(left, 60)
                push_log(f"[INFO] {acc['username']} on cooldown {m}m{s_rem}s")
                time.sleep(1)
                continue
            user = acc["username"]
            session = acc["session"]
            n_changes = random.randint(max(1, changes_per_account-1), changes_per_account)
            push_log(f"[INFO] Using account {user} for ~{n_changes} changes")
            did_block = False
            for change_round in range(n_changes):
                if worker_stop.is_set(): break
                for name in names:
                    if worker_stop.is_set(): break
                    for tid in thread_ids:
                        if suffix_random:
                            unique_name = f"{name}_{random.randint(1000,9999)}"
                        else:
                            unique_name = name
                        ok, resp, _, blocked = safe_change_group_name(tid, unique_name, session, delay)
                        push_log(f"[{user}] {resp}")
                        worker_state["stats"]["changes"] += 1
                        if blocked:
                            rest_seconds = random.randint(rest_min*60, rest_max*60)
                            acc["rest_until"] = time.time() + rest_seconds
                            push_log(f"[WARN] {user} blocked: resting {rest_seconds//60}m")
                            did_block = True
                            break
                        time.sleep(delay)
                    if did_block or worker_stop.is_set(): break
                if did_block or worker_stop.is_set(): break
            time.sleep(random.uniform(0.5, 1.5))
    except Exception as e:
        push_log(f"[ERROR] Worker exception: {e}")
    finally:
        worker_state["running"] = False
        push_log("[INFO] Worker stopped")

# ---------- HTML template ----------
INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>IG GC Changer - Web</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {margin:0;font-family:sans-serif;background:url('https://i.imgur.com/3ZQ3ZQy.jpg') no-repeat center center fixed;background-size:cover;color:#eee;}
.container {max-width:1100px;margin:30px auto;background:rgba(0,0,0,0.6);padding:24px;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,0.6);}
h1{text-align:center;margin:0 0 10px 0;font-size:34px;color:#ffcc00;text-shadow:0 2px 8px #000;}
.sub{text-align:center;margin-bottom:18px;color:#ddd;}
form .row{display:flex;gap:12px;}
.col{flex:1;}
label{display:block;margin-bottom:6px;font-weight:700;color:#ffd;}
input[type=text], input[type=number]{width:100%;padding:12px;border-radius:8px;border:1px solid #333;background:rgba(255,255,255,0.03);color:#fff;}
.bigbox{padding:18px;background: rgba(255,255,255,0.02);border-radius:10px;}
.controls{text-align:center;margin-top:12px;display:flex;gap:12px;justify-content:center;}
button{padding:10px 18px;border-radius:8px;border:none;cursor:pointer;font-weight:700;}
button.start{background:linear-gradient(90deg,#28a745,#66d17b);color:#042;}
button.stop{background:linear-gradient(90deg,#dc3545,#ff7b86);color:#420;}
.logs{margin-top:16px;background: rgba(0,0,0,0.7);padding:12px;border-radius:8px;max-height:420px;overflow:auto;font-family:monospace;color:#fff;}
.footer{margin-top:14px;text-align:center;color:#ccc;font-size:14px;}
.header-links{ text-align:center; margin-bottom:12px;}
.header-links div{ color:#fff; margin:4px 0; font-weight:700; }
</style>
</head>
<body>
<div class="container">
<h1>ULTRA SPEED — IG GC CHANGER</h1>
<div class="header-links bigbox">
<div style="color:#7fffd4">WHATSAPP: +918115048433</div>
<div style="color:#87cefa">FACEBOOK: https://www.facebook.com/share/19jV7wnTgz/</div>
<div style="color:#ff78c2">INSTAGRAM: ayu_sh9343</div>
<div style="color:#ffd36b">TOOLS OWNER: YK TRICKS INDIA</div>
</div>
<form id="cfgForm" onsubmit="return startWorker();">
<div class="bigbox">
<label>Accounts (comma separated, format username:password)</label>
<input type="text" id="accounts" placeholder="user1:pass1,user2:pass2,..." required>
<div style="height:12px"></div>
<label>Group thread IDs (comma separated)</label>
<input type="text" id="thread_ids" placeholder="1234567890,9876543210" required>
<div style="height:12px"></div>
<label>Names (comma separated)</label>
<input type="text" id="names" placeholder="Ayush,Rajput,King" required>
<div style="height:12px"></div>
<div class="row">
<div class="col">
<label>Delay (seconds)</label>
<input type="number" id="delay" min="0.2" step="0.1" value="2">
</div>
<div class="col">
<label>Changes per account</label>
<input type="number" id="changes_per_account" min="1" step="1" value="5">
</div>
</div>
<div style="height:12px"></div>
<div class="row">
<div class="col">
<label>Rest minutes if blocked (min)</label>
<input type="number" id="rest_min" min="1" value="10">
</div>
<div class="col">
<label>Rest minutes if blocked (max)</label>
<input type="number" id="rest_max" min="1" value="15">
</div>
</div>
<div style="height:12px"></div>
<div class="controls">
<button type="submit" class="start">Start</button>
<button type="button" class="stop" onclick="stopWorker();">Stop</button>
</div>
</div>
</form>
<div class="logs" id="logs"><em>Logs will appear here...</em></div>
<div class="footer">Run locally only. Keep credentials safe.</div>
</div>
<script>
let eventSource = null;
function appendLog(line){const el=document.getElementById('logs');const text=line.replace(/</g,"&lt;").replace(/>/g,"&gt;");const p=document.createElement('div');p.innerHTML=text;el.appendChild(p);el.scrollTop=el.scrollHeight;}
function startWorker(){const accounts=document.getElementById('accounts').value.trim();const thread_ids=document.getElementById('thread_ids').value.trim();const names=document.getElementById('names').value.trim();const delay=parseFloat(document.getElementById('delay').value)||2;const changes_per_account=parseInt(document.getElementById('changes_per_account').value)||5;const rest_min=parseInt(document.getElementById('rest_min').value)||10;const rest_max=parseInt(document.getElementById('rest_max').value)||15;if(!accounts||!thread_ids||!names){alert("Fill accounts, thread IDs, names");return false;}fetch('/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({accounts, thread_ids, names, delay, changes_per_account, rest_min, rest_max})}).then(resp=>resp.json()).then(data=>{if(data.status==='ok'){appendLog('[INFO] Worker started');startSSE();}else{alert('Start failed: '+(data.error||'unknown'));}}).catch(e=>{alert('Start request failed:'+e);});return false;}
function stopWorker(){fetch('/stop',{method:'POST'}).then(r=>r.json()).then(data=>{appendLog('[INFO] Stop requested');closeSSE();}).catch(e=>{appendLog('[ERROR] stop request failed');});}
function startSSE(){closeSSE();eventSource=new EventSource('/stream');eventSource.onmessage=function(e){if(!e.data)return;appendLog(e.data);};eventSource.onerror=function(e){appendLog('[WARN] SSE connection error');};}
function closeSSE(){if(eventSource){eventSource.close();eventSource=null;}}
</script>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index(): return render_template_string(INDEX_HTML)

@app.route("/start", methods=["POST"])
def start():
    global worker_thread, worker_stop, worker_state
    if worker_state.get("running"): return jsonify({"status":"error","error":"Worker already running"})
    data=request.get_json(force=True)
    try:
        accounts_raw = data.get("accounts","")
        acc_pairs=[]
        for part in accounts_raw.split(","):
            part=part.strip()
            if not part: continue
            if ":" not in part: return jsonify({"status":"error","error":"Accounts must be username:password"})
            u,p=part.split(":",1)
            acc_pairs.append((u.strip(),p.strip()))
        thread_ids=[t.strip() for t in data.get("thread_ids","").split(",") if t.strip()]
        names=[n.strip() for n in data.get("names","").split(",") if n.strip()]
        delay=float(data.get("delay",2))
        changes_per_account=int(data.get("changes_per_account",5))
        rest_min=int(data.get("rest_min",10))
        rest_max=int(data.get("rest_max",15))
    except Exception as e: return jsonify({"status":"error","error":f"Invalid input: {e}"})
    if not acc_pairs or not thread_ids or not names: return jsonify({"status":"error","error":"Missing accounts/thread_ids/names"})
    worker_stop.clear()
    cfg={"accounts":acc_pairs,"thread_ids":thread_ids,"names":names,"delay":delay,"changes_per_account":changes_per_account,"rest_min":rest_min,"rest_max":rest_max,"suffix_random":True}
    worker_thread=threading.Thread(target=worker_loop,args=(cfg,),daemon=True)
    worker_thread.start()
    return jsonify({"status":"ok"})

@app.route("/stop", methods=["POST"])
def stop(): worker_stop.set(); return jsonify({"status":"ok"})

@app.route("/stream")
def stream(): return Response(stream_logs(), mimetype='text/event-stream')

if __name__=="__main__":
    print("Run this app: python app.py (open http://127.0.0.1:5000)")
    app.run(host="0.0.0.0", threaded=True)
    