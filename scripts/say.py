#!/usr/bin/env python3
"""
Simple speech output for Claude. Just speak text directly.

Usage:
    python -m scripts.say "Hello, I am reading the manuscript now."
    python -m scripts.say --voice kore "Let me explain what's happening here."
"""

import os
import sys
import tempfile
import wave

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

DEFAULT_VOICE = "Orus"  # Deep, measured - good for scholarly narration


def say(text: str, voice: str = DEFAULT_VOICE):
    """Generate and play speech."""
    client = genai.Client()

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice,
                    )
                )
            ),
        )
    )

    audio_data = response.candidates[0].content.parts[0].inline_data.data

    # Save to temp file and play
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        with wave.open(f.name, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_data)

        # Play on Windows
        os.startfile(f.name)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.say \"text to speak\"")
        sys.exit(1)

    voice = DEFAULT_VOICE
    text_args = []

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--voice" and i + 1 < len(sys.argv):
            voice = sys.argv[i + 1]
            i += 2
        else:
            text_args.append(sys.argv[i])
            i += 1

    text = " ".join(text_args)
    say(text, voice)
