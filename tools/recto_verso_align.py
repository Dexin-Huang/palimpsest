"""
Interactive recto-verso alignment and bleed removal.
Allows manual adjustment of alignment parameters.
"""

import cv2
import numpy as np
from PIL import Image
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import base64
import webbrowser
import threading
import argparse
import sys

# Global storage
recto = None
verso = None
recto_gray = None
verso_gray = None

def process_alignment(offset_x, offset_y, scale, rotation, blend, verso_weight):
    """Apply alignment and subtraction with given parameters."""
    global recto, verso, recto_gray, verso_gray

    h, w = recto.shape[:2]

    # Flip verso horizontally
    verso_flipped = cv2.flip(verso, 1)
    verso_gray_flipped = cv2.flip(verso_gray, 1)

    # Build transformation matrix
    center = (w // 2, h // 2)

    # Rotation and scale
    M = cv2.getRotationMatrix2D(center, rotation, scale)

    # Add translation
    M[0, 2] += offset_x
    M[1, 2] += offset_y

    # Apply transform to verso
    verso_aligned = cv2.warpAffine(verso_flipped, M, (w, h), borderValue=(255, 255, 255))
    verso_gray_aligned = cv2.warpAffine(verso_gray_flipped, M, (w, h), borderValue=255)

    # Convert to float
    recto_f = recto.astype(np.float32) / 255.0
    verso_f = verso_aligned.astype(np.float32) / 255.0

    # Create bleed mask
    verso_ink = verso_gray_aligned < 128  # Verso ink areas
    recto_medium = (recto_gray > 76) & (recto_gray < 217)  # Recto bleed areas
    bleed_mask = (verso_ink & recto_medium).astype(np.float32)

    # Smooth the mask
    bleed_mask = cv2.GaussianBlur(bleed_mask, (21, 21), 0)
    bleed_mask_3ch = np.stack([bleed_mask] * 3, axis=-1)

    # Invert verso (ink becomes bright)
    verso_inv = 1.0 - verso_f

    # Apply correction
    correction = verso_inv * verso_weight * bleed_mask_3ch
    result = recto_f + correction * blend
    result = np.clip(result, 0, 1)

    return (result * 255).astype(np.uint8), (bleed_mask * 255).astype(np.uint8)


def create_server(recto_path, verso_path, output_path):
    global recto, verso, recto_gray, verso_gray

    # Load images
    recto = cv2.imread(recto_path)
    verso = cv2.imread(verso_path)

    # Resize for preview
    max_dim = 1000
    h, w = recto.shape[:2]
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        recto = cv2.resize(recto, (int(w * scale), int(h * scale)))
        verso = cv2.resize(verso, (int(w * scale), int(h * scale)))

    recto_gray = cv2.cvtColor(recto, cv2.COLOR_BGR2GRAY)
    verso_gray = cv2.cvtColor(verso, cv2.COLOR_BGR2GRAY)

    HTML = '''<!DOCTYPE html>
<html>
<head>
    <title>Recto-Verso Alignment</title>
    <style>
        body { font-family: Arial; margin: 20px; background: #1a1a1a; color: #fff; }
        .container { display: flex; gap: 20px; }
        .controls { width: 350px; }
        .image-container { flex: 1; }
        img { max-width: 100%; border: 1px solid #444; }
        label { display: block; margin: 12px 0 5px; }
        input[type="range"] { width: 100%; }
        .value { color: #0af; font-weight: bold; }
        button { margin-top: 20px; padding: 12px 24px; background: #0af; border: none; cursor: pointer; font-size: 16px; }
        button:hover { background: #08f; }
        .section { margin-top: 20px; padding-top: 15px; border-top: 1px solid #444; }
        h3 { margin: 0 0 10px 0; color: #0af; }
    </style>
</head>
<body>
    <h1>Recto-Verso Alignment Tool</h1>
    <div class="container">
        <div class="controls">
            <h3>Alignment</h3>

            <label>X Offset: <span class="value" id="ox_val">0</span>px</label>
            <input type="range" id="offset_x" min="-200" max="200" value="0" oninput="updateImage()">

            <label>Y Offset: <span class="value" id="oy_val">0</span>px</label>
            <input type="range" id="offset_y" min="-200" max="200" value="0" oninput="updateImage()">

            <label>Scale: <span class="value" id="scale_val">1.00</span></label>
            <input type="range" id="scale" min="0.9" max="1.1" value="1.0" step="0.005" oninput="updateImage()">

            <label>Rotation: <span class="value" id="rot_val">0.0</span>°</label>
            <input type="range" id="rotation" min="-5" max="5" value="0" step="0.1" oninput="updateImage()">

            <div class="section">
                <h3>Bleed Removal</h3>

                <label>Blend: <span class="value" id="blend_val">0.6</span></label>
                <input type="range" id="blend" min="0" max="1" value="0.6" step="0.05" oninput="updateImage()">

                <label>Verso Weight: <span class="value" id="vw_val">0.8</span></label>
                <input type="range" id="verso_weight" min="0" max="1.5" value="0.8" step="0.05" oninput="updateImage()">
            </div>

            <button onclick="saveImage()">💾 Save Full Resolution</button>

            <p style="margin-top:20px; color:#888; font-size:12px;">
                Adjust alignment until bleed-through on recto matches ink on verso (flipped).
                Then tune Blend/Verso Weight to remove bleed while preserving text.
            </p>
        </div>
        <div class="image-container">
            <img id="result" src="">
        </div>
    </div>
    <script>
        let debounceTimer;

        function updateImage() {
            document.getElementById('ox_val').textContent = document.getElementById('offset_x').value;
            document.getElementById('oy_val').textContent = document.getElementById('offset_y').value;
            document.getElementById('scale_val').textContent = parseFloat(document.getElementById('scale').value).toFixed(2);
            document.getElementById('rot_val').textContent = parseFloat(document.getElementById('rotation').value).toFixed(1);
            document.getElementById('blend_val').textContent = document.getElementById('blend').value;
            document.getElementById('vw_val').textContent = document.getElementById('verso_weight').value;

            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(fetchImage, 150);
        }

        function fetchImage() {
            const params = new URLSearchParams({
                offset_x: document.getElementById('offset_x').value,
                offset_y: document.getElementById('offset_y').value,
                scale: document.getElementById('scale').value,
                rotation: document.getElementById('rotation').value,
                blend: document.getElementById('blend').value,
                verso_weight: document.getElementById('verso_weight').value
            });

            fetch('/process?' + params)
                .then(r => r.text())
                .then(data => {
                    document.getElementById('result').src = 'data:image/jpeg;base64,' + data;
                });
        }

        function saveImage() {
            const params = new URLSearchParams({
                offset_x: document.getElementById('offset_x').value,
                offset_y: document.getElementById('offset_y').value,
                scale: document.getElementById('scale').value,
                rotation: document.getElementById('rotation').value,
                blend: document.getElementById('blend').value,
                verso_weight: document.getElementById('verso_weight').value
            });

            fetch('/save?' + params)
                .then(r => r.text())
                .then(path => {
                    alert('Saved to: ' + path);
                });
        }

        updateImage();
    </script>
</body>
</html>'''

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            if self.path == '/' or self.path.startswith('/index'):
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(HTML.encode())

            elif self.path.startswith('/process'):
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)

                ox = int(params.get('offset_x', [0])[0])
                oy = int(params.get('offset_y', [0])[0])
                sc = float(params.get('scale', [1.0])[0])
                rot = float(params.get('rotation', [0])[0])
                blend = float(params.get('blend', [0.6])[0])
                vw = float(params.get('verso_weight', [0.8])[0])

                result, _ = process_alignment(ox, oy, sc, rot, blend, vw)

                _, buffer = cv2.imencode('.jpg', result, [cv2.IMWRITE_JPEG_QUALITY, 85])
                img_data = base64.b64encode(buffer).decode('utf-8')

                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(img_data.encode())

            elif self.path.startswith('/save'):
                # For save, reload full-res images and process
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)

                ox = int(params.get('offset_x', [0])[0])
                oy = int(params.get('offset_y', [0])[0])
                sc = float(params.get('scale', [1.0])[0])
                rot = float(params.get('rotation', [0])[0])
                blend = float(params.get('blend', [0.6])[0])
                vw = float(params.get('verso_weight', [0.8])[0])

                # Load full res
                recto_full = cv2.imread(recto_path)
                verso_full = cv2.imread(verso_path)

                # Scale parameters for full res
                h_preview, w_preview = recto.shape[:2]
                h_full, w_full = recto_full.shape[:2]
                scale_factor = h_full / h_preview

                ox_full = int(ox * scale_factor)
                oy_full = int(oy * scale_factor)

                # Process full res
                verso_flipped = cv2.flip(verso_full, 1)
                center = (w_full // 2, h_full // 2)
                M = cv2.getRotationMatrix2D(center, rot, sc)
                M[0, 2] += ox_full
                M[1, 2] += oy_full

                verso_aligned = cv2.warpAffine(verso_flipped, M, (w_full, h_full), borderValue=(255,255,255))

                recto_f = recto_full.astype(np.float32) / 255.0
                verso_f = verso_aligned.astype(np.float32) / 255.0

                recto_gray_full = cv2.cvtColor(recto_full, cv2.COLOR_BGR2GRAY)
                verso_gray_aligned = cv2.cvtColor(verso_aligned, cv2.COLOR_BGR2GRAY)

                verso_ink = verso_gray_aligned < 128
                recto_medium = (recto_gray_full > 76) & (recto_gray_full < 217)
                bleed_mask = (verso_ink & recto_medium).astype(np.float32)
                bleed_mask = cv2.GaussianBlur(bleed_mask, (21, 21), 0)
                bleed_mask_3ch = np.stack([bleed_mask] * 3, axis=-1)

                verso_inv = 1.0 - verso_f
                correction = verso_inv * vw * bleed_mask_3ch
                result = recto_f + correction * blend
                result = np.clip(result, 0, 1)
                result = (result * 255).astype(np.uint8)

                cv2.imwrite(output_path, result)

                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(output_path.encode())

            else:
                self.send_response(404)
                self.end_headers()

    port = 8767
    server = HTTPServer(('localhost', port), Handler)
    url = f'http://localhost:{port}'
    print(f"Opening {url}")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("recto", help="Recto (front) image")
    parser.add_argument("verso", help="Verso (back) image")
    parser.add_argument("-o", "--output", default="cleaned_aligned.png")
    args = parser.parse_args()

    create_server(args.recto, args.verso, args.output)
