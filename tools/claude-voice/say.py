#!/usr/bin/env python3
"""
Send speech to the Claude Voice Companion.

Usage:
    python -m tools.claude-voice.say "Hello, this is what I wanted to say."
"""

import json
import socket
import sys

HOST = "127.0.0.1"
PORT = 52849


def say(text: str) -> bool:
    """Send text to the voice companion for speech."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((HOST, PORT))
        sock.send(json.dumps({"text": text}).encode("utf-8"))
        sock.close()
        return True
    except ConnectionRefusedError:
        print("Voice companion not running. Start it with:")
        print("  python tools/claude-voice/voice_companion.py")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tools.claude-voice.say \"text to speak\"")
        sys.exit(1)

    text = " ".join(sys.argv[1:])
    success = say(text)
    sys.exit(0 if success else 1)
