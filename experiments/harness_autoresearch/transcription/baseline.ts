import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const TRANSCRIPTION_POLICY = `Act as a diplomatic manuscript transcriber.
Read the staged page image carefully and reproduce the visible source text without commentary.
Preserve meaningful line breaks and call submit_transcription exactly once with the final text.`;

export default function baselineTranscription(pi: ExtensionAPI) {
  pi.on("before_agent_start", async (event) => ({
    systemPrompt: [...event.systemPrompt, TRANSCRIPTION_POLICY],
  }));
}
