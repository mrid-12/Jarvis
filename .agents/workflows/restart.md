---
description: Restarts all application processes by killing existing ones and rerunning backend/client.
---

// turbo-all
1. Kill all running Python processes to ensure a clean state.
```powershell
taskkill /F /IM python.exe /T
```

2. Start the Backend server.
```powershell
# Run in c:\Users\mrid9\.gemini\antigravity\scratch\gemini_live_agent\backend
C:\Users\mrid9\.gemini\antigravity\scratch\gemini_live_agent\.venv\Scripts\python.exe main.py
```

3. Start the Client application.
```powershell
# Run in c:\Users\mrid9\.gemini\antigravity\scratch\gemini_live_agent\client
C:\Users\mrid9\.gemini\antigravity\scratch\gemini_live_agent\.venv\Scripts\python.exe app.py
```
