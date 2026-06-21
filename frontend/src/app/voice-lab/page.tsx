import type { Metadata } from "next";
import { PageHeader } from "@/components/layout/PageHeader";
import { MicTranscriber } from "@/components/experimental/MicTranscriber";

export const metadata: Metadata = {
  title: "Voice Lab · Shepherd",
  description: "Experimental: record your microphone and transcribe it via Deepgram.",
};

export default function VoiceLabPage() {
  return (
    <div>
      <PageHeader
        title="Voice Lab"
        subtitle="Experimental · record (or upload) audio and transcribe it via the Deepgram backend."
      />
      <div className="p-6">
        <MicTranscriber />
      </div>
    </div>
  );
}
