"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, Square } from "lucide-react";
import { Button } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

type Status = "idle" | "recording" | "transcribing";

/**
 * Record a short voice command, transcribe it via the backend Deepgram proxy
 * (`/api/deepgram/transcribe`), and hand the text back to the caller — which
 * forwards it to the selected agent as an intent.
 */
export function MicCommandButton({
  onTranscript,
  onError,
  disabled,
}: {
  onTranscript: (text: string) => void;
  onError?: (message: string) => void;
  disabled?: boolean;
}) {
  const [status, setStatus] = useState<Status>("idle");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  const transcribe = useCallback(
    async (blob: Blob) => {
      setStatus("transcribing");
      const form = new FormData();
      form.append("file", blob, "command.webm");
      try {
        const res = await fetch("/api/deepgram/transcribe", { method: "POST", body: form });
        const data = (await res.json()) as { transcript?: string; error?: string };
        if (!res.ok || data.error) {
          onError?.(data.error ?? `transcription failed (${res.status})`);
        } else if (data.transcript?.trim()) {
          onTranscript(data.transcript.trim());
        } else {
          onError?.("empty transcript — try speaking longer");
        }
      } catch (e) {
        onError?.((e as Error).message);
      } finally {
        setStatus("idle");
      }
    },
    [onError, onTranscript],
  );

  const start = useCallback(async () => {
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      onError?.("microphone recording not supported in this browser");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => e.data.size > 0 && chunksRef.current.push(e.data);
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || "audio/webm" });
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        void transcribe(blob);
      };
      recorderRef.current = mr;
      mr.start();
      setStatus("recording");
    } catch (e) {
      onError?.(`mic access denied: ${(e as Error).message}`);
    }
  }, [onError, transcribe]);

  const stop = useCallback(() => {
    setStatus("transcribing");
    recorderRef.current?.stop();
  }, []);

  if (status === "recording") {
    return (
      <Button variant="danger" onClick={stop}>
        <Square size={14} /> Stop
      </Button>
    );
  }
  return (
    <Button
      variant="outline"
      onClick={start}
      disabled={disabled || status === "transcribing"}
      className={cn(status === "transcribing" && "opacity-70")}
    >
      {status === "transcribing" ? (
        <Loader2 size={15} className="animate-spin" />
      ) : (
        <Mic size={15} />
      )}
      {status === "transcribing" ? "Transcribing" : "Speak"}
    </Button>
  );
}
