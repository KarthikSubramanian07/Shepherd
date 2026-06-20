"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AudioLines,
  CheckCircle2,
  KeyRound,
  Mic,
  Square,
  Upload,
  XCircle,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  Input,
  Spinner,
} from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

type Status = "idle" | "recording" | "transcribing";

interface TranscribeResult {
  transcript?: string;
  filename?: string;
  bytes?: number;
  model?: string;
  language?: string;
  error?: string;
}

function fmtTime(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function MicTranscriber() {
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TranscribeResult | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [keyConfigured, setKeyConfigured] = useState<boolean | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const audioUrlRef = useRef<string | null>(null);

  // Keep a ref of the current object URL so cleanup never has a stale closure.
  useEffect(() => {
    audioUrlRef.current = audioUrl;
  }, [audioUrl]);

  // Probe the backend so we can tell the user whether a key is already set.
  useEffect(() => {
    let active = true;
    fetch("/api/deepgram/status")
      .then((r) => r.json())
      .then((d) => active && setKeyConfigured(Boolean(d?.configured)))
      .catch(() => active && setKeyConfigured(false));
    return () => {
      active = false;
    };
  }, []);

  // Teardown on unmount: stop timer, mic tracks, and free the audio URL.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      streamRef.current?.getTracks().forEach((t) => t.stop());
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    };
  }, []);

  const stopTimer = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  }, []);

  const setNewAudio = useCallback((blob: Blob) => {
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    setAudioUrl(URL.createObjectURL(blob));
  }, []);

  const transcribe = useCallback(
    async (blob: Blob, filename: string) => {
      setStatus("transcribing");
      setError(null);
      setResult(null);
      const form = new FormData();
      form.append("file", blob, filename);
      if (apiKey.trim()) form.append("api_key", apiKey.trim());
      try {
        const res = await fetch("/api/deepgram/transcribe", {
          method: "POST",
          body: form,
        });
        const data = (await res.json()) as TranscribeResult;
        if (!res.ok) {
          setError(data.error ?? `transcription failed (${res.status})`);
        } else {
          setResult(data);
        }
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setStatus("idle");
      }
    },
    [apiKey],
  );

  const startRecording = useCallback(async () => {
    setError(null);
    setResult(null);
    if (
      typeof MediaRecorder === "undefined" ||
      !navigator.mediaDevices?.getUserMedia
    ) {
      setError("This browser does not support microphone recording.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const mr = new MediaRecorder(stream);
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: mr.mimeType || "audio/webm",
        });
        setNewAudio(blob);
        stream.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
        void transcribe(blob, "recording.webm");
      };
      recorderRef.current = mr;
      mr.start();
      setStatus("recording");
      setElapsed(0);
      timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    } catch (e) {
      setError(`Microphone access denied or unavailable: ${(e as Error).message}`);
    }
  }, [setNewAudio, transcribe]);

  const stopRecording = useCallback(() => {
    stopTimer();
    setStatus("transcribing");
    recorderRef.current?.stop();
  }, [stopTimer]);

  const onFile = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0];
      if (!f) return;
      setNewAudio(f);
      void transcribe(f, f.name);
      e.target.value = "";
    },
    [setNewAudio, transcribe],
  );

  const recording = status === "recording";
  const transcribing = status === "transcribing";
  const busy = recording || transcribing;

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      {/* Key status */}
      <Card className="flex items-center justify-between p-3">
        <div className="flex items-center gap-2 text-sm">
          <KeyRound size={15} className="text-muted" />
          <span className="text-muted">Backend Deepgram key:</span>
          {keyConfigured === null ? (
            <span className="text-muted">checking…</span>
          ) : keyConfigured ? (
            <Badge tone="ok">
              <CheckCircle2 size={12} /> configured
            </Badge>
          ) : (
            <Badge tone="flag">
              <XCircle size={12} /> not set
            </Badge>
          )}
        </div>
        <span className="text-[11px] text-muted">
          set <code className="text-ink">DEEPGRAM_API_KEY</code> in backend .env
        </span>
      </Card>

      {/* Recorder */}
      <Card className="p-6">
        <div className="flex flex-col items-center gap-4">
          <div
            className={cn(
              "flex h-20 w-20 items-center justify-center rounded-full border transition-colors",
              recording
                ? "border-halt/50 bg-halt/10 text-halt"
                : "border-edge bg-panel2 text-muted",
            )}
          >
            {recording ? (
              <AudioLines size={30} className="animate-pulse" />
            ) : (
              <Mic size={30} />
            )}
          </div>

          <div className="text-center">
            <div className="font-mono text-2xl tabular-nums text-ink">
              {fmtTime(elapsed)}
            </div>
            <div className="text-xs text-muted">
              {recording
                ? "Recording — click stop when done"
                : transcribing
                  ? "Transcribing…"
                  : "Ready"}
            </div>
          </div>

          <div className="flex items-center gap-2">
            {!recording ? (
              <Button onClick={startRecording} disabled={transcribing}>
                <Mic size={15} /> Record
              </Button>
            ) : (
              <Button variant="danger" onClick={stopRecording}>
                <Square size={14} /> Stop
              </Button>
            )}

            <label>
              <input
                type="file"
                accept="audio/*"
                className="hidden"
                onChange={onFile}
                disabled={busy}
              />
              <span
                className={cn(
                  "inline-flex h-9 cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-edge px-3.5 text-sm font-medium text-ink transition-colors hover:bg-panel2",
                  busy && "pointer-events-none opacity-50",
                )}
              >
                <Upload size={15} /> Upload file
              </span>
            </label>
          </div>
        </div>

        {/* Optional per-request key override */}
        <div className="mt-5">
          <label className="mb-1 block text-[11px] uppercase tracking-wide text-muted">
            API key override (optional)
          </label>
          <Input
            type="password"
            placeholder="Leave blank to use the backend's configured key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            autoComplete="off"
          />
        </div>

        {audioUrl && (
          <audio controls src={audioUrl} className="mt-4 w-full">
            <track kind="captions" />
          </audio>
        )}
      </Card>

      {/* Result / error */}
      {transcribing && (
        <Card className="flex items-center gap-2 p-4 text-sm text-muted">
          <Spinner size={16} /> Sending audio to Deepgram…
        </Card>
      )}

      {error && (
        <Card className="border-halt/30 bg-halt/5 p-4">
          <div className="flex items-center gap-2 text-sm text-halt">
            <XCircle size={16} /> {error}
          </div>
        </Card>
      )}

      {result?.transcript !== undefined && (
        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold text-ink">Transcript</span>
            <div className="flex items-center gap-2 text-[11px] text-muted">
              {result.model && <Badge tone="accent">{result.model}</Badge>}
              {result.language && <span>{result.language}</span>}
              {typeof result.bytes === "number" && (
                <span>{(result.bytes / 1024).toFixed(1)} KB</span>
              )}
            </div>
          </div>
          {result.transcript ? (
            <p className="whitespace-pre-wrap text-sm text-ink">
              {result.transcript}
            </p>
          ) : (
            <p className="text-sm italic text-muted">
              (empty transcript — try speaking louder or longer)
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
