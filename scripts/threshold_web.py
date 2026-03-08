"""
Web-based threshold tool for manuscript cleanup.
Opens in browser with sliders to control threshold.
"""
import cv2
import numpy as np
import base64
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import webbrowser
import threading
from pathlib import Path

# Global image storage
original_gray = None
image_path = None


def default_image_path() -> str | None:
    for candidate in Path("library").glob("*/images/*.jpg"):
        return str(candidate)
    return None

def process_image(thresh_val, mode, block_size, c_offset, invert):
    global original_gray

    if block_size < 3:
        block_size = 3
    if block_size % 2 == 0:
        block_size += 1

    if mode == 0:
        thresh_type = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
        _, result = cv2.threshold(original_gray, thresh_val, 255, thresh_type)
    else:
        thresh_type = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
        result = cv2.adaptiveThreshold(
            original_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            thresh_type, block_size, c_offset
        )

    _, buffer = cv2.imencode('.jpg', result, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buffer).decode('utf-8')

HTML_TEMPLATE = '''<!DOCTYPE html>
<html>
<head>
    <title>Threshold Tool</title>
    <style>
        body { font-family: Arial; margin: 20px; background: #1a1a1a; color: #fff; }
        .container { display: flex; gap: 20px; }
        .controls { width: 300px; }
        .image-container { flex: 1; }
        img { max-width: 100%; border: 1px solid #444; }
        label { display: block; margin: 15px 0 5px; }
        input[type="range"] { width: 100%; }
        .value { color: #0af; font-weight: bold; }
        button { margin-top: 20px; padding: 10px 20px; background: #0af; border: none; cursor: pointer; }
        button:hover { background: #08f; }
        select { width: 100%; padding: 5px; }
    </style>
</head>
<body>
    <h1>Threshold Tool - Bleed-through Removal</h1>
    <div class="container">
        <div class="controls">
            <label>Mode:
                <select id="mode" onchange="updateImage()">
                    <option value="0">Global Threshold</option>
                    <option value="1" selected>Adaptive (recommended)</option>
                </select>
            </label>

            <label>Threshold (global): <span class="value" id="thresh_val">128</span></label>
            <input type="range" id="threshold" min="0" max="255" value="128" oninput="updateImage()">

            <label>Block Size (adaptive): <span class="value" id="block_val">11</span></label>
            <input type="range" id="block_size" min="3" max="99" value="11" step="2" oninput="updateImage()">

            <label>C Offset (adaptive): <span class="value" id="c_val">8</span></label>
            <input type="range" id="c_offset" min="0" max="50" value="8" oninput="updateImage()">

            <label>
                <input type="checkbox" id="invert" onchange="updateImage()"> Invert
            </label>

            <button onclick="saveImage()">Save Image</button>
            <p style="color:#888; font-size:12px;">
                For bleed-through: Use Adaptive mode, increase C Offset to push faint marks to white.
            </p>
        </div>
        <div class="image-container">
            <img id="result" src="">
        </div>
    </div>
    <script>
        let debounceTimer;

        function updateImage() {
            document.getElementById('thresh_val').textContent = document.getElementById('threshold').value;
            document.getElementById('block_val').textContent = document.getElementById('block_size').value;
            document.getElementById('c_val').textContent = document.getElementById('c_offset').value;

            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(fetchImage, 100);
        }

        function fetchImage() {
            const params = new URLSearchParams({
                threshold: document.getElementById('threshold').value,
                mode: document.getElementById('mode').value,
                block_size: document.getElementById('block_size').value,
                c_offset: document.getElementById('c_offset').value,
                invert: document.getElementById('invert').checked ? 1 : 0
            });

            fetch('/process?' + params)
                .then(r => r.text())
                .then(data => {
                    document.getElementById('result').src = 'data:image/jpeg;base64,' + data;
                });
        }

        function saveImage() {
            const params = new URLSearchParams({
                threshold: document.getElementById('threshold').value,
                mode: document.getElementById('mode').value,
                block_size: document.getElementById('block_size').value,
                c_offset: document.getElementById('c_offset').value,
                invert: document.getElementById('invert').checked ? 1 : 0,
                save: 1
            });

            fetch('/process?' + params)
                .then(r => r.text())
                .then(data => {
                    alert('Saved!');
                });
        }

        // Initial load
        updateImage();
    </script>
</body>
</html>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress logging

    def do_GET(self):
        global image_path

        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode())

        elif self.path.startswith('/process'):
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)

            thresh = int(params.get('threshold', [128])[0])
            mode = int(params.get('mode', [1])[0])
            block = int(params.get('block_size', [11])[0])
            c_off = int(params.get('c_offset', [8])[0])
            invert = int(params.get('invert', [0])[0])
            save = int(params.get('save', [0])[0])

            img_data = process_image(thresh, mode, block, c_off, invert)

            if save:
                # Save the processed image
                out_path = image_path.replace('.jpg', '_cleaned.png')
                img_bytes = base64.b64decode(img_data)
                with open(out_path, 'wb') as f:
                    f.write(img_bytes)
                print(f"Saved to: {out_path}")

            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(img_data.encode())

        else:
            self.send_response(404)
            self.end_headers()

def main(img_path):
    global original_gray, image_path
    image_path = img_path

    img = cv2.imread(img_path)
    if img is None:
        print(f"Could not load: {img_path}")
        return

    # Resize for performance
    max_height = 1200
    h, w = img.shape[:2]
    if h > max_height:
        scale = max_height / h
        img = cv2.resize(img, (int(w * scale), int(h * scale)))

    original_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    port = 8765
    server = HTTPServer(('localhost', port), Handler)

    url = f'http://localhost:{port}'
    print(f"Opening browser at {url}")
    print("Press Ctrl+C to stop")

    # Open browser after short delay
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
        server.shutdown()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        img = default_image_path()
        if not img:
            print("Usage: python scripts/threshold_web.py <image_path>")
            sys.exit(1)
    else:
        img = sys.argv[1]
    main(img)
