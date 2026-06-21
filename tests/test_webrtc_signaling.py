"""
End-to-end test for WebRTC signaling relay through the coordinator.

Spins up the coordinator, connects a mock agent and mock UI, and verifies that
webrtc.offer, webrtc.answer, and webrtc.ice messages relay correctly in both
directions.
"""
import asyncio
import json
import os
import sys
import time

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run_test():
    import websockets

    # Start the coordinator in a subprocess.
    import subprocess

    env = {
        **os.environ,
        "COORDINATOR_PORT": "18770",
        "COORDINATOR_TOKEN": "test-token",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "coordinator"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Give coordinator time to start.
    await asyncio.sleep(2)

    base = "ws://localhost:18770"
    token = "test-token"
    code = "TEST_SESSION"

    results = {
        "offer_relayed_to_ui": False,
        "answer_relayed_to_agent": False,
        "ice_from_agent_relayed_to_ui": False,
        "ice_from_ui_relayed_to_agent": False,
    }

    try:
        # Connect agent.
        agent_url = f"{base}/agent?agent_id=test-agent&name=TestAgent&host=localhost&code={code}&token={token}"
        agent_ws = await websockets.connect(agent_url, max_size=None)

        # Send hello.
        await agent_ws.send(json.dumps({
            "type": "hello",
            "name": "TestAgent",
            "host": "localhost",
            "protocol_version": 1,
        }))

        await asyncio.sleep(0.5)

        # Connect UI.
        ui_url = f"{base}/ui?code={code}&token={token}"
        ui_ws = await websockets.connect(ui_url, max_size=None)

        # UI receives initial roster.
        roster_msg = json.loads(await asyncio.wait_for(ui_ws.recv(), timeout=3))
        assert roster_msg["type"] == "agents", f"Expected agents roster, got {roster_msg['type']}"
        assert len(roster_msg["agents"]) == 1, "Expected 1 agent in roster"
        print(f"  [OK] UI received roster with 1 agent")

        # UI watches the agent.
        await ui_ws.send(json.dumps({"type": "watch", "agent_id": "test-agent"}))
        await asyncio.sleep(0.3)

        # --- Test 1: Agent sends webrtc.offer → UI receives it ---
        fake_offer = {"type": "offer", "sdp": "v=0\r\n...fake SDP offer..."}
        await agent_ws.send(json.dumps({
            "type": "webrtc.offer",
            "data": fake_offer,
        }))

        msg = json.loads(await asyncio.wait_for(ui_ws.recv(), timeout=3))
        assert msg["type"] == "webrtc.offer", f"Expected webrtc.offer, got {msg['type']}"
        assert msg["agent_id"] == "test-agent"
        assert msg["data"] == fake_offer
        results["offer_relayed_to_ui"] = True
        print(f"  [OK] webrtc.offer relayed from agent to UI")

        # --- Test 2: UI sends webrtc.answer → Agent receives it ---
        fake_answer = {"type": "answer", "sdp": "v=0\r\n...fake SDP answer..."}
        await ui_ws.send(json.dumps({
            "type": "webrtc.answer",
            "agent_id": "test-agent",
            "data": fake_answer,
        }))

        msg = json.loads(await asyncio.wait_for(agent_ws.recv(), timeout=3))
        assert msg["type"] == "webrtc.answer", f"Expected webrtc.answer, got {msg['type']}"
        assert msg["data"] == fake_answer
        results["answer_relayed_to_agent"] = True
        print(f"  [OK] webrtc.answer relayed from UI to agent")

        # --- Test 3: Agent sends webrtc.ice → UI receives it ---
        fake_ice_from_agent = {"candidate": "candidate:1 1 UDP 2130706431 10.0.0.1 1234 typ host"}
        await agent_ws.send(json.dumps({
            "type": "webrtc.ice",
            "data": fake_ice_from_agent,
        }))

        msg = json.loads(await asyncio.wait_for(ui_ws.recv(), timeout=3))
        assert msg["type"] == "webrtc.ice", f"Expected webrtc.ice, got {msg['type']}"
        assert msg["agent_id"] == "test-agent"
        assert msg["data"] == fake_ice_from_agent
        results["ice_from_agent_relayed_to_ui"] = True
        print(f"  [OK] webrtc.ice relayed from agent to UI")

        # --- Test 4: UI sends webrtc.ice → Agent receives it ---
        fake_ice_from_ui = {"candidate": "candidate:2 1 UDP 1694498815 192.168.1.1 5678 typ srflx"}
        await ui_ws.send(json.dumps({
            "type": "webrtc.ice",
            "agent_id": "test-agent",
            "data": fake_ice_from_ui,
        }))

        msg = json.loads(await asyncio.wait_for(agent_ws.recv(), timeout=3))
        assert msg["type"] == "webrtc.ice", f"Expected webrtc.ice, got {msg['type']}"
        assert msg["data"] == fake_ice_from_ui
        results["ice_from_ui_relayed_to_agent"] = True
        print(f"  [OK] webrtc.ice relayed from UI to agent")

        await agent_ws.close()
        await ui_ws.close()

    finally:
        proc.terminate()
        proc.wait(timeout=5)

    # Summary
    print("\n  === Results ===")
    all_passed = True
    for k, v in results.items():
        status = "PASS" if v else "FAIL"
        if not v:
            all_passed = False
        print(f"  [{status}] {k}")

    if all_passed:
        print("\n  All WebRTC signaling tests passed!")
    else:
        print("\n  SOME TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    print("\n  WebRTC Signaling Relay E2E Test")
    print("  " + "=" * 40)
    asyncio.run(run_test())
