"use client";

/**
 * WebRTC P2P screen streaming for the Remote Command Center.
 *
 * Instead of relaying base64 JPEG frames through the coordinator (bandwidth-
 * heavy), this module negotiates a direct peer-to-peer video connection between
 * the agent and the operator's browser. The coordinator only forwards the tiny
 * signaling messages (SDP offer/answer + ICE candidates).
 *
 * Flow:
 *   1. UI sends "watch" for an agent → coordinator relays
 *   2. Agent sends "webrtc.offer" (SDP) through coordinator → arrives here
 *   3. We create an RTCPeerConnection, set remote description, create answer
 *   4. We send "webrtc.answer" back through coordinator → agent
 *   5. ICE candidates trickle both ways through coordinator
 *   6. Once connected, video frames flow DIRECTLY agent → UI (no coordinator)
 *
 * If WebRTC negotiation fails, the system falls back to the existing base64
 * frame relay transparently (no user action needed).
 */

import { useCallback, useEffect, useRef, useState } from "react";

const ICE_SERVERS: RTCIceServer[] = [
  { urls: "stun:stun.l.google.com:19302" },
  { urls: "stun:stun1.l.google.com:19302" },
  { urls: "stun:stun2.l.google.com:19302" },
];

export type WebRTCState = "idle" | "connecting" | "connected" | "failed";

export interface WebRTCApi {
  /** Current connection state. */
  state: WebRTCState;
  /** Callback ref to attach to a <video> element for rendering the remote stream. */
  videoRef: (el: HTMLVideoElement | null) => void;
  /** Handle an incoming signaling message from the coordinator. */
  handleSignal: (type: string, data: unknown) => void;
  /** Tear down the current connection. */
  close: () => void;
}

/**
 * React hook that manages a WebRTC peer connection for receiving an agent's
 * screen stream. Call `handleSignal` whenever the coordinator delivers a
 * `webrtc.offer` or `webrtc.ice` message for the watched agent.
 *
 * @param sendSignal - callback to send signaling messages back through the
 *                     coordinator (type + data payload).
 * @param agentId - the agent we're receiving from (resets connection on change).
 */
export function useWebRTC(
  sendSignal: (type: string, data: unknown) => void,
  agentId: string | null,
): WebRTCApi {
  const [state, setState] = useState<WebRTCState>("idle");
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const videoElRef = useRef<HTMLVideoElement | null>(null);
  const pendingCandidates = useRef<RTCIceCandidateInit[]>([]);
  const disconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const videoRef = useCallback((el: HTMLVideoElement | null) => {
    videoElRef.current = el;
    // Re-attach the active stream when the element swaps (e.g. fullscreen toggle).
    if (el && pcRef.current) {
      const receivers = pcRef.current.getReceivers();
      const stream = receivers.length > 0 ? new MediaStream(receivers.map((r) => r.track).filter(Boolean)) : null;
      if (stream && stream.getTracks().length > 0) {
        el.srcObject = stream;
      }
    }
  }, []);

  const close = useCallback(() => {
    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }
    if (disconnectTimer.current) {
      clearTimeout(disconnectTimer.current);
      disconnectTimer.current = null;
    }
    pendingCandidates.current = [];
    setState("idle");
    if (videoElRef.current) {
      videoElRef.current.srcObject = null;
    }
  }, []);

  // Reset when the watched agent changes or component unmounts.
  useEffect(() => {
    close();
    return () => { close(); };
  }, [agentId, close]);

  const handleSignal = useCallback(
    (type: string, data: unknown) => {
      if (!agentId) return;

      if (type === "webrtc.offer") {
        // Agent sent an SDP offer — create peer connection + answer.
        (async () => {
          try {
            // Preserve ICE candidates buffered for the incoming connection.
            const savedCandidates = [...pendingCandidates.current];
            close();
            pendingCandidates.current = savedCandidates;
            setState("connecting");

            const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
            pcRef.current = pc;

            // Forward our ICE candidates to the agent via coordinator.
            pc.onicecandidate = (ev) => {
              if (ev.candidate) {
                sendSignal("webrtc.ice", {
                  candidate: ev.candidate.toJSON(),
                });
              }
            };

            pc.oniceconnectionstatechange = () => {
              const s = pc.iceConnectionState;
              if (disconnectTimer.current) {
                clearTimeout(disconnectTimer.current);
                disconnectTimer.current = null;
              }
              if (s === "connected" || s === "completed") {
                setState("connected");
              } else if (s === "failed") {
                setState("failed");
              } else if (s === "disconnected") {
                // Transient — give ICE 10s to recover before falling back.
                disconnectTimer.current = setTimeout(() => {
                  if (pc.iceConnectionState === "disconnected") {
                    setState("failed");
                  }
                }, 10_000);
              }
            };

            // Receive remote video track.
            pc.ontrack = (ev) => {
              if (videoElRef.current && ev.streams[0]) {
                videoElRef.current.srcObject = ev.streams[0];
              }
            };

            const offer = data as RTCSessionDescriptionInit;
            await pc.setRemoteDescription(new RTCSessionDescription(offer));

            // Flush any ICE candidates that arrived before the offer.
            for (const c of pendingCandidates.current) {
              await pc.addIceCandidate(new RTCIceCandidate(c));
            }
            pendingCandidates.current = [];

            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            sendSignal("webrtc.answer", answer);
          } catch (err) {
            console.error("[webrtc] offer handling failed:", err);
            setState("failed");
          }
        })();
      } else if (type === "webrtc.ice") {
        const payload = data as { candidate: RTCIceCandidateInit };
        if (!payload?.candidate) return;
        const pc = pcRef.current;
        if (pc && pc.remoteDescription) {
          pc.addIceCandidate(new RTCIceCandidate(payload.candidate)).catch(
            (err) => console.warn("[webrtc] addIceCandidate failed:", err),
          );
        } else {
          // Buffer until remote description is set.
          pendingCandidates.current.push(payload.candidate);
        }
      }
    },
    [agentId, close, sendSignal],
  );

  return { state, videoRef, handleSignal, close };
}
