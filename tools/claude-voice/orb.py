#!/usr/bin/env python3
"""
Claude Voice Orb - A minimal floating orb for voice interaction.

Features:
- Floating translucent orb UI
- Click to listen (speech-to-text via Whisper)
- Receives text from Claude and speaks it (Gemini TTS)
- Native audio playback (no external apps)

Usage:
    python tools/claude-voice/orb.py

Dependencies:
    pip install sounddevice numpy openai-whisper google-genai
"""

import io
import json
import math
import queue
import socket
import struct
import threading
import time
import tkinter as tk
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

# Conditional imports
try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print("Whisper not installed. STT disabled. Install with: pip install openai-whisper")

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# Configuration
HOST = "127.0.0.1"
PORT = 52849
VOICE = "Aoede"  # Serene, warm
SAMPLE_RATE = 16000  # For recording (Whisper expects 16kHz)
TTS_SAMPLE_RATE = 24000  # Gemini TTS output rate

# Orb appearance
ORB_SIZE = 80
COLORS = {
    "idle": "#3d3d5c",
    "idle_glow": "#5c5c8a",
    "listening": "#4a90d9",
    "listening_glow": "#7ab8ff",
    "speaking": "#9b6dcc",
    "speaking_glow": "#c49eff",
}


class VoiceOrb:
    def __init__(self):
        self.client = genai.Client()
        self.speech_queue = queue.Queue()
        self.running = True
        self.state = "idle"  # idle, listening, speaking
        self.animation_phase = 0

        # Audio state
        self.is_recording = False
        self.recorded_frames = []

        # Whisper model (load lazily)
        self.whisper_model = None

        # Transcript output (for Claude Code to see)
        self.last_transcript = ""

        self.setup_ui()

        # Start threads
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.speaker_thread = threading.Thread(target=self.process_speech_queue, daemon=True)
        self.server_thread.start()
        self.speaker_thread.start()

        # Start animation
        self.animate()

    def setup_ui(self):
        """Create the floating orb UI."""
        self.root = tk.Tk()
        self.root.title("")
        self.root.geometry(f"{ORB_SIZE + 20}x{ORB_SIZE + 20}")
        self.root.overrideredirect(True)  # No title bar
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "black")
        self.root.configure(bg="black")

        # Position in bottom right
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = screen_w - ORB_SIZE - 50
        y = screen_h - ORB_SIZE - 100
        self.root.geometry(f"+{x}+{y}")

        # Canvas for the orb
        self.canvas = tk.Canvas(
            self.root,
            width=ORB_SIZE + 20,
            height=ORB_SIZE + 20,
            bg="black",
            highlightthickness=0
        )
        self.canvas.pack()

        # Draw initial orb
        center = (ORB_SIZE + 20) // 2
        self.orb_glow = self.canvas.create_oval(
            center - ORB_SIZE//2 - 5,
            center - ORB_SIZE//2 - 5,
            center + ORB_SIZE//2 + 5,
            center + ORB_SIZE//2 + 5,
            fill=COLORS["idle_glow"],
            outline=""
        )
        self.orb = self.canvas.create_oval(
            center - ORB_SIZE//2 + 5,
            center - ORB_SIZE//2 + 5,
            center + ORB_SIZE//2 - 5,
            center + ORB_SIZE//2 - 5,
            fill=COLORS["idle"],
            outline=""
        )

        # Bindings
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonPress-1>", self.start_drag)

        # Right-click to quit
        self.canvas.bind("<Button-3>", lambda e: self.quit())

        # Drag state
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_threshold = 5
        self.is_drag = False

    def start_drag(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.is_drag = False

    def on_drag(self, event):
        dx = abs(event.x - self.drag_start_x)
        dy = abs(event.y - self.drag_start_y)
        if dx > self.drag_threshold or dy > self.drag_threshold:
            self.is_drag = True
            x = self.root.winfo_x() + event.x - self.drag_start_x
            y = self.root.winfo_y() + event.y - self.drag_start_y
            self.root.geometry(f"+{x}+{y}")

    def on_click(self, event):
        # Only trigger if not dragging
        if not self.is_drag:
            self.root.after(100, self.toggle_listening)

    def toggle_listening(self):
        """Toggle recording state."""
        if self.state == "idle":
            self.start_listening()
        elif self.state == "listening":
            self.stop_listening()

    def start_listening(self):
        """Start recording audio."""
        if not WHISPER_AVAILABLE:
            print("Whisper not available")
            return

        self.state = "listening"
        self.is_recording = True
        self.recorded_frames = []

        # Start recording in background
        threading.Thread(target=self._record_audio, daemon=True).start()

    def stop_listening(self):
        """Stop recording and transcribe."""
        self.is_recording = False
        self.state = "idle"

        if self.recorded_frames:
            # Transcribe in background
            threading.Thread(target=self._transcribe_audio, daemon=True).start()

    def _record_audio(self):
        """Record audio from microphone."""
        def callback(indata, frames, time_info, status):
            if self.is_recording:
                self.recorded_frames.append(indata.copy())

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32', callback=callback):
            while self.is_recording:
                time.sleep(0.1)

    def _transcribe_audio(self):
        """Transcribe recorded audio using Whisper."""
        if not self.recorded_frames:
            return

        # Load model if needed
        if self.whisper_model is None:
            print("Loading Whisper model...")
            self.whisper_model = whisper.load_model("base")

        # Combine frames
        audio = np.concatenate(self.recorded_frames, axis=0).flatten()

        # Transcribe
        result = self.whisper_model.transcribe(audio, fp16=False)
        transcript = result["text"].strip()

        if transcript:
            self.last_transcript = transcript
            print(f"\n[You said]: {transcript}\n")

            # Copy to clipboard for easy pasting
            self.root.clipboard_clear()
            self.root.clipboard_append(transcript)

    def set_state(self, state: str):
        """Update orb state and appearance."""
        self.state = state

    def animate(self):
        """Animate the orb based on state."""
        self.animation_phase += 0.15

        # Pulse effect
        pulse = math.sin(self.animation_phase) * 0.5 + 0.5

        if self.state == "idle":
            glow_color = COLORS["idle_glow"]
            orb_color = COLORS["idle"]
            scale = 1.0 + pulse * 0.02
        elif self.state == "listening":
            glow_color = COLORS["listening_glow"]
            orb_color = COLORS["listening"]
            scale = 1.0 + pulse * 0.08
        elif self.state == "speaking":
            glow_color = COLORS["speaking_glow"]
            orb_color = COLORS["speaking"]
            scale = 1.0 + pulse * 0.05
        else:
            glow_color = COLORS["idle_glow"]
            orb_color = COLORS["idle"]
            scale = 1.0

        # Update colors
        self.canvas.itemconfig(self.orb_glow, fill=glow_color)
        self.canvas.itemconfig(self.orb, fill=orb_color)

        # Update size (subtle pulse)
        center = (ORB_SIZE + 20) // 2
        half_size = (ORB_SIZE // 2) * scale
        glow_extra = 5 * scale

        self.canvas.coords(
            self.orb_glow,
            center - half_size - glow_extra,
            center - half_size - glow_extra,
            center + half_size + glow_extra,
            center + half_size + glow_extra
        )
        self.canvas.coords(
            self.orb,
            center - half_size + 5,
            center - half_size + 5,
            center + half_size - 5,
            center + half_size - 5
        )

        # Continue animation
        if self.running:
            self.root.after(50, self.animate)

    def generate_speech(self, text: str) -> bytes:
        """Generate speech audio from text."""
        response = self.client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=VOICE,
                        )
                    )
                ),
            )
        )
        return response.candidates[0].content.parts[0].inline_data.data

    def play_audio(self, pcm_data: bytes):
        """Play audio natively using sounddevice."""
        # Convert bytes to numpy array (16-bit PCM)
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        # Normalize to float32 for sounddevice
        audio_float = audio_array.astype(np.float32) / 32768.0
        # Play
        sd.play(audio_float, TTS_SAMPLE_RATE)
        sd.wait()  # Wait until finished

    def process_speech_queue(self):
        """Background thread: process queued speech requests."""
        while self.running:
            try:
                text = self.speech_queue.get(timeout=0.5)

                # Update state
                self.root.after(0, lambda: self.set_state("speaking"))

                try:
                    audio_data = self.generate_speech(text)
                    self.play_audio(audio_data)
                except Exception as e:
                    print(f"Speech error: {e}")

                self.root.after(0, lambda: self.set_state("idle"))

            except queue.Empty:
                continue

    def run_server(self):
        """Background thread: listen for speech requests from Claude."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server.bind((HOST, PORT))
            server.listen(5)
            server.settimeout(1.0)

            while self.running:
                try:
                    conn, addr = server.accept()
                    data = conn.recv(65536).decode("utf-8")
                    conn.close()

                    if data:
                        try:
                            msg = json.loads(data)
                            text = msg.get("text", "")
                            if text:
                                self.speech_queue.put(text)
                        except json.JSONDecodeError:
                            self.speech_queue.put(data)

                except socket.timeout:
                    continue

        finally:
            server.close()

    def quit(self):
        self.running = False
        self.root.quit()

    def run(self):
        """Start the orb."""
        print("Claude Voice Orb running")
        print("- Click orb to start/stop listening")
        print("- Right-click to quit")
        print("- Drag to move")
        print(f"- Listening for speech on port {PORT}")
        self.root.mainloop()


def main():
    orb = VoiceOrb()
    orb.run()


if __name__ == "__main__":
    main()
