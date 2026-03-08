#!/usr/bin/env python3
"""
Claude Voice Companion - A small UI for Claude's speech.

Runs as a persistent process. Claude Code instances send text via a local socket,
and this app speaks them through Gemini TTS.

Usage:
    python voice_companion.py

To send speech from Claude Code:
    python -m tools.claude-voice.say "Hello, I wanted to tell you something."
"""

import json
import os
import queue
import socket
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import font as tkfont
import wave
import winsound
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment from palimpsest root
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)

# Configuration
HOST = "127.0.0.1"
PORT = 52849  # Arbitrary high port
VOICE = "Aoede"  # Serene, warm, storytelling
WINDOW_WIDTH = 400
WINDOW_HEIGHT = 120


class VoiceCompanion:
    def __init__(self):
        self.client = genai.Client()
        self.speech_queue = queue.Queue()
        self.is_speaking = False
        self.running = True

        # Set up the UI
        self.setup_ui()

        # Start background threads
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.speaker_thread = threading.Thread(target=self.process_speech_queue, daemon=True)
        self.server_thread.start()
        self.speaker_thread.start()

    def setup_ui(self):
        """Create the minimal, elegant UI."""
        self.root = tk.Tk()
        self.root.title("Claude")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.configure(bg="#1a1a2e")
        self.root.attributes("-topmost", True)
        self.root.resizable(True, True)

        # Remove title bar but keep it draggable
        self.root.overrideredirect(True)

        # Main frame with subtle border
        self.frame = tk.Frame(
            self.root,
            bg="#1a1a2e",
            highlightbackground="#4a4a6a",
            highlightthickness=1
        )
        self.frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Header bar for dragging
        self.header = tk.Frame(self.frame, bg="#252542", height=24)
        self.header.pack(fill=tk.X, side=tk.TOP)
        self.header.pack_propagate(False)

        # Title in header
        title_font = tkfont.Font(family="Segoe UI", size=9)
        self.title_label = tk.Label(
            self.header,
            text="Claude",
            bg="#252542",
            fg="#8888aa",
            font=title_font
        )
        self.title_label.pack(side=tk.LEFT, padx=8)

        # Close button
        self.close_btn = tk.Label(
            self.header,
            text="  ×  ",
            bg="#252542",
            fg="#666688",
            font=title_font,
            cursor="hand2"
        )
        self.close_btn.pack(side=tk.RIGHT)
        self.close_btn.bind("<Button-1>", lambda e: self.quit())
        self.close_btn.bind("<Enter>", lambda e: self.close_btn.configure(fg="#ff6b6b"))
        self.close_btn.bind("<Leave>", lambda e: self.close_btn.configure(fg="#666688"))

        # Make header draggable
        self.header.bind("<Button-1>", self.start_drag)
        self.header.bind("<B1-Motion>", self.on_drag)
        self.title_label.bind("<Button-1>", self.start_drag)
        self.title_label.bind("<B1-Motion>", self.on_drag)

        # Content area
        self.content = tk.Frame(self.frame, bg="#1a1a2e")
        self.content.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # Status indicator (subtle dot)
        self.status_frame = tk.Frame(self.content, bg="#1a1a2e")
        self.status_frame.pack(anchor=tk.W)

        self.status_dot = tk.Canvas(
            self.status_frame,
            width=8,
            height=8,
            bg="#1a1a2e",
            highlightthickness=0
        )
        self.status_dot.pack(side=tk.LEFT, pady=4)
        self.dot = self.status_dot.create_oval(1, 1, 7, 7, fill="#4a4a6a", outline="")

        self.status_label = tk.Label(
            self.status_frame,
            text="listening",
            bg="#1a1a2e",
            fg="#6a6a8a",
            font=tkfont.Font(family="Segoe UI", size=8)
        )
        self.status_label.pack(side=tk.LEFT, padx=6)

        # Text display
        text_font = tkfont.Font(family="Segoe UI", size=10)
        self.text_display = tk.Label(
            self.content,
            text="",
            bg="#1a1a2e",
            fg="#c4c4dc",
            font=text_font,
            wraplength=WINDOW_WIDTH - 40,
            justify=tk.LEFT,
            anchor=tk.NW
        )
        self.text_display.pack(fill=tk.BOTH, expand=True, pady=4)

        # Drag state
        self.drag_start_x = 0
        self.drag_start_y = 0

    def start_drag(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def on_drag(self, event):
        x = self.root.winfo_x() + event.x - self.drag_start_x
        y = self.root.winfo_y() + event.y - self.drag_start_y
        self.root.geometry(f"+{x}+{y}")

    def set_status(self, status: str, speaking: bool = False):
        """Update status indicator."""
        self.status_label.configure(text=status)
        color = "#7c6fa0" if speaking else "#4a4a6a"  # Soft purple when speaking
        self.status_dot.itemconfig(self.dot, fill=color)

    def set_text(self, text: str):
        """Update displayed text (truncated for UI)."""
        display_text = text[:150] + "..." if len(text) > 150 else text
        self.text_display.configure(text=display_text)

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

    def play_audio(self, audio_data: bytes):
        """Save and play audio."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            with wave.open(f.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(audio_data)
            os.startfile(f.name)

    def process_speech_queue(self):
        """Background thread: process queued speech requests."""
        while self.running:
            try:
                text = self.speech_queue.get(timeout=0.5)

                # Update UI
                self.root.after(0, lambda t=text: self.set_text(t))
                self.root.after(0, lambda: self.set_status("speaking", True))

                # Generate and play
                try:
                    audio_data = self.generate_speech(text)
                    self.play_audio(audio_data)
                except Exception as e:
                    print(f"Speech error: {e}")

                # Estimate speech duration (rough: ~150 words/min)
                words = len(text.split())
                duration = max(2, words / 2.5)  # seconds

                import time
                time.sleep(duration)

                self.root.after(0, lambda: self.set_status("listening", False))
                self.root.after(0, lambda: self.set_text(""))

            except queue.Empty:
                continue

    def run_server(self):
        """Background thread: listen for speech requests."""
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
                            # Plain text fallback
                            self.speech_queue.put(data)

                except socket.timeout:
                    continue

        finally:
            server.close()

    def quit(self):
        self.running = False
        self.root.quit()

    def run(self):
        """Start the UI."""
        self.root.mainloop()


def main():
    app = VoiceCompanion()
    app.run()


if __name__ == "__main__":
    main()
