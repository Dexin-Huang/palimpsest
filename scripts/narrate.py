#!/usr/bin/env python3
"""
Narrate manuscript pages using Gemini TTS.

Usage:
    python -m scripts.narrate --page library/vatican_borg_cin_377/exports/book/pages/f002r_normalized.txt
    python -m scripts.narrate --text "Hello, this is a test."
    python -m scripts.narrate --page f002r_normalized.txt --voice Charon --play
"""

import argparse
import os
import subprocess
import sys
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Available voices (Gemini 2.5 TTS)
# Deep/authoritative: Charon, Orus, Fenrir
# Warm/narrative: Kore, Puck, Aoede
# Clear/neutral: Zephyr, Leda
VOICES = {
    "charon": "Charon",    # deep, authoritative - good for imperial decrees
    "kore": "Kore",        # warm, clear
    "puck": "Puck",        # engaging, narrative
    "zephyr": "Zephyr",    # neutral, clear
    "aoede": "Aoede",      # warm, storytelling
    "orus": "Orus",        # deep, measured
    "fenrir": "Fenrir",    # deep, dramatic
    "leda": "Leda",        # clear, professional
}

DEFAULT_VOICE = "Charon"  # Good for historical documents


def generate_speech(text: str, voice: str = DEFAULT_VOICE, style_prompt: str = None) -> bytes:
    """Generate speech audio from text using Gemini TTS."""
    client = genai.Client()

    # Build the prompt with optional style instructions
    if style_prompt:
        content = f"{style_prompt}\n\n{text}"
    else:
        # Default style for manuscript reading
        content = (
            "Read this in a measured, scholarly tone, as if narrating a historical document. "
            "Pause appropriately at quotations and section breaks. "
            "Give weight to the Emperor's words when reading his decrees.\n\n"
            f"{text}"
        )

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=content,
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

    return response.candidates[0].content.parts[0].inline_data.data


def save_wave(filename: str, pcm_data: bytes, channels: int = 1, rate: int = 24000, sample_width: int = 2):
    """Save PCM audio data to a WAV file."""
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm_data)


def play_audio(filepath: str):
    """Play audio file using system default player."""
    if sys.platform == "win32":
        os.startfile(filepath)
    elif sys.platform == "darwin":
        subprocess.run(["afplay", filepath])
    else:
        # Linux - try common players
        for player in ["aplay", "paplay", "mpv", "ffplay"]:
            try:
                subprocess.run([player, filepath], check=True)
                break
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue


def main():
    parser = argparse.ArgumentParser(description="Narrate manuscript pages using Gemini TTS")
    parser.add_argument("--page", "-p", type=str, help="Path to a page file to narrate")
    parser.add_argument("--text", "-t", type=str, help="Direct text to narrate")
    parser.add_argument("--voice", "-v", type=str, default=DEFAULT_VOICE,
                        choices=list(VOICES.keys()), help=f"Voice to use (default: {DEFAULT_VOICE.lower()})")
    parser.add_argument("--style", "-s", type=str, help="Custom style prompt for the narration")
    parser.add_argument("--output", "-o", type=str, help="Output WAV file path (default: auto-generated)")
    parser.add_argument("--play", action="store_true", help="Play audio after generating")
    parser.add_argument("--list-voices", action="store_true", help="List available voices")

    args = parser.parse_args()

    if args.list_voices:
        print("Available voices:")
        for key, name in VOICES.items():
            print(f"  {key}: {name}")
        return

    if not args.page and not args.text:
        parser.error("Either --page or --text must be provided")

    # Get text to narrate
    if args.page:
        page_path = Path(args.page)
        if not page_path.exists():
            print(f"Error: Page file not found: {page_path}")
            sys.exit(1)
        text = page_path.read_text(encoding="utf-8")
        default_output = page_path.with_suffix(".wav")
    else:
        text = args.text
        default_output = Path("narration.wav")

    # Resolve voice name
    voice = VOICES.get(args.voice.lower(), args.voice)

    # Generate output path
    output_path = Path(args.output) if args.output else default_output

    print(f"Generating speech with voice '{voice}'...")
    print(f"Text length: {len(text)} characters")

    try:
        audio_data = generate_speech(text, voice=voice, style_prompt=args.style)
        save_wave(str(output_path), audio_data)
        print(f"Saved to: {output_path}")

        if args.play:
            print("Playing audio...")
            play_audio(str(output_path))

    except Exception as e:
        print(f"Error generating speech: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
