@echo off
wt ^
new-tab --title "backend :8000" cmd /k "cd /d D:\lovable livekit && python backend\main.py" ^
; new-tab --title "agent start" cmd /k "cd /d D:\lovable livekit\agent && python main.py start" ^
; new-tab --title "frontend :5173" cmd /k "cd /d D:\lovable livekit\frontend\entameen-main && npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort"
