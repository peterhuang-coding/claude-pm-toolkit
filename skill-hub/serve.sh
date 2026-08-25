#!/bin/bash
# skill-hub 启动脚本：http://127.0.0.1:3458
cd "$HOME/skill-hub" || exit 1
exec python3 -m uvicorn server:app --host 127.0.0.1 --port 3458
