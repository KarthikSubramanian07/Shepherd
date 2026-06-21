# Shepherd E2E Testing with Devin Sessions

How to run a full end-to-end test of the Shepherd coordinator + agent dispatch using Devin child sessions as separate machines.

---

## Architecture

```
┌─────────────────────────────────────┐
│  Parent Devin Session (this VM)     │
│                                     │
│  coordinator/server.py (port 8770)  │
│  + cloudflared tunnel (public URL)  │
│  + main.py --listen (agent role)    │
│                                     │
│  Orchestrates the test, sends       │
│  commands, monitors output          │
└─────────────────────────────────────┘
            ▲
            │ public wss:// via Cloudflare Tunnel
            ▼
┌─────────────────────────────────────┐
│  Child Devin Session (separate VM)  │
│                                     │
│  Command Center (Next.js frontend)  │
│  Opens localhost:3000/remote         │
│  Watches agent, sends commands,     │
│  records the session                │
└─────────────────────────────────────┘
```

**Why this layout:** The parent session runs both the coordinator AND the agent because the agent needs API keys (from session secrets) that can't be passed to child sessions. The child session runs the Command Center UI (no secrets needed — just the public tunnel URL + token).

---

## Prerequisites

On the parent session:
- Shepherd repo cloned with `uv sync` completed
- `.env` configured with LLM API keys (Gemini or Anthropic)
- `cloudflared` installed
- `gnome-screenshot` installed (for pyautogui)

---

## Step-by-Step

### 1. Start the Coordinator (Parent Session)

```bash
cd /home/ubuntu/repos/shepherd
export COORDINATOR_TOKEN="shepherd-demo-2024"
export COORDINATOR_PORT=8770
uv run python -m coordinator &
```

### 2. Start the Cloudflare Tunnel (Parent Session)

```bash
cloudflared tunnel --url http://localhost:8770 2>&1 | tee /tmp/tunnel.log &
# Wait for URL to appear, then extract it:
sleep 5
TUNNEL_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/tunnel.log | head -1)
echo "Tunnel: $TUNNEL_URL"
```

### 3. Start the Agent (Parent Session)

```bash
cd /home/ubuntu/repos/shepherd

# Add coordinator config to .env
cat >> .env << EOF
COORDINATOR_URL=wss://${TUNNEL_URL#https://}
COORDINATOR_TOKEN=shepherd-demo-2024
AGENT_PAIRING_CODE=E2E-TEST
AGENT_ID=test-agent
AGENT_NAME=TestAgent
RELAY_FPS=1
RELAY_FRAME_WIDTH=640
RELAY_FRAME_QUALITY=45
EXECUTION_MODE=AUTONOMOUS
EOF

# Start (ensure DISPLAY and XAUTHORITY are set)
DISPLAY=:0 XAUTHORITY=/home/ubuntu/.Xauthority uv run python main.py --listen
```

Verify:
```bash
curl "$TUNNEL_URL/api/health?token=shepherd-demo-2024"
# → {"ok":true,"agents":1,"protocol_version":1}
```

### 4. Launch Command Center Child Session

Use `devin_session_create` with this prompt (substitute the actual tunnel URL):

```
Clone https://github.com/KarthikSubramanian07/shepherd.git
cd frontend
cat > .env.local << 'EOF'
NEXT_PUBLIC_COORDINATOR_URL=https://<TUNNEL_URL>
NEXT_PUBLIC_COORDINATOR_TOKEN=shepherd-demo-2024
EOF
npm install && npm run dev &
sleep 20
Open Chrome to http://localhost:3000/remote
Enter session code: E2E-TEST
Click Connect
Wait for agent to appear in Fleet
Take a screenshot
Type "Navigate to https://example.com" in the task input
Press Enter
Wait 60 seconds
Take another screenshot
Report what happened
```

### 5. Send a Command Programmatically (Alternative to UI)

Instead of using the Command Center UI, you can send intents directly:

```python
import asyncio, websockets, json

async def send_intent(tunnel_url, token, code, agent_id, text):
    url = f"wss://{tunnel_url}/ui?token={token}&code={code}"
    async with websockets.connect(url) as ws:
        await ws.recv()  # roster
        await ws.send(json.dumps({
            "type": "command",
            "command": "intent",
            "payload": {"text": text},
            "agent_id": agent_id
        }))
        # Listen for execution events
        async for msg in ws:
            data = json.loads(msg)
            if data.get("type") == "event":
                evt = data["event"]
                print(f"{evt['type']}: {evt.get('data', {})}")
```

### 6. Verify Full Dispatch

Watch the agent's output for:
```
REMOTE    intent from command-center: "Navigate to https://example.com"
INTENT    "Navigate to https://example.com" (via typed)
ROUTE     -> AUTONOMOUS  conf=1.0
EXEC      start AUTONOMOUS mode=AUTONOMOUS steps=30
  STEP 0 start  [agent_s] Navigate to https://example.com
  STEP 0 thinking...
```

If Agent S is configured with a valid API key, it will:
1. Take a screenshot of the current browser
2. Send it to the LLM with the navigation goal
3. Generate pyautogui code to click the address bar and type the URL
4. Execute the code
5. Verify navigation succeeded

---

## Common Issues

| Issue | Fix |
|-------|-----|
| `pyautogui.screenshot()` fails | Set `XAUTHORITY=/home/ubuntu/.Xauthority` and install `gnome-screenshot` |
| Agent S "unavailable" | Run `uv sync` (installs `gui-agents`) and set `AGENT_S_ENGINE_TYPE` + API key |
| Gemini 429 quota error | Free tier daily limit hit; switch to `AGENT_S_ENGINE_TYPE=anthropic` |
| Child can't connect to coordinator | Verify the Cloudflare tunnel is still running; re-run `cloudflared` if needed |
| "Waiting for frames" in UI | Agent not connected yet; wait 30s or check agent logs |
| Routine matched instead of autonomous | Set `EXECUTION_MODE=AUTONOMOUS` in .env for free-form goals |

---

## Cleanup

```bash
# Kill background processes
kill %1 %2  # coordinator, tunnel
# Or kill by name
pkill -f "python -m coordinator"
pkill -f cloudflared
```
