"""Serve a blinded, local writer-identity adjudication quiz."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
REPORT_PATH = OUT / "report.json"
KEY_PATH = OUT / "blind_identity_key.json"
QUIZ_PATH = OUT / "blind_identity_quiz.html"
ANSWER_PATH = OUT / "human_blind_review.json"
PRIMARY = "p3477_calibrated"
REQUIRED_WINS = 10
CHOICES = {"A", "B", "C", "Tie"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def render_quiz(report: dict, key: dict) -> str:
    observations = {
        item["crop_id"]: item for item in report["observations"]
    }
    rows = []
    for index, (crop_id, blind) in enumerate(key.items(), start=1):
        item = observations[crop_id]
        candidates = []
        for label in ("A", "B", "C"):
            system = blind[label]
            image = data_url(resolve(item["systems"][system]["path"]))
            candidates.append(
                f"""
                <label class="candidate" data-choice="{label}">
                  <input type="radio" name="{crop_id}" value="{label}">
                  <img src="{image}" alt="Candidate {label} for {blind['character']}">
                  <span>Candidate {label}</span>
                </label>"""
            )
        real = data_url(resolve(item["crop_path"]))
        kai = data_url(resolve(item["systems"]["kai"]["path"]))
        rows.append(
            f"""
            <section class="question" data-crop="{crop_id}" data-index="{index}">
              <header>
                <span class="number">{index:02d}</span>
                <div><strong>{blind['character']}</strong><small>Which candidate best preserves this writer's identity?</small></div>
              </header>
              <div class="comparison">
                <figure class="reference real"><img src="{real}" alt="Real manuscript ink"><figcaption>Real P.3477 ink</figcaption></figure>
                <figure class="reference kai"><img src="{kai}" alt="Canonical Kai control"><figcaption>Kai content control</figcaption></figure>
                {''.join(candidates)}
              </div>
              <label class="tie"><input type="radio" name="{crop_id}" value="Tie"><span>No clear winner / tie</span></label>
            </section>"""
        )
    crop_ids = json.dumps(list(key), ensure_ascii=False)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>P.3477 blind writer-identity review</title>
<style>
:root {{ color-scheme: light; --ink:#171713; --paper:#f2efe6; --card:#fbfaf5; --line:#c9c4b5; --accent:#9b2f24; --muted:#69665d; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font:16px/1.45 Arial, sans-serif; }}
main {{ width:min(1500px, calc(100% - 32px)); margin:0 auto; padding:42px 0 120px; }}
.intro {{ display:grid; grid-template-columns:1fr auto; gap:32px; align-items:end; border-bottom:2px solid var(--ink); padding-bottom:24px; margin-bottom:28px; }}
h1 {{ margin:0 0 8px; font-size:clamp(30px,4vw,58px); letter-spacing:-.035em; }}
.intro p {{ max-width:820px; margin:0; color:var(--muted); font-size:18px; }}
.progress {{ text-align:right; font-weight:700; font-size:20px; }}
.progress span {{ color:var(--accent); font-size:34px; }}
.question {{ background:var(--card); border:1px solid var(--line); margin:18px 0; padding:20px; box-shadow:0 8px 24px rgba(29,27,20,.05); }}
.question.missing {{ outline:3px solid var(--accent); }}
.question header {{ display:flex; gap:14px; align-items:center; margin-bottom:16px; }}
.number {{ font:700 13px/1 monospace; background:var(--ink); color:var(--paper); padding:8px; }}
.question strong {{ display:inline-block; font:700 34px/1 serif; margin-right:14px; }}
.question small {{ color:var(--muted); font-size:15px; }}
.comparison {{ display:grid; grid-template-columns:repeat(5,minmax(130px,1fr)); gap:14px; }}
figure, .candidate {{ margin:0; min-width:0; }}
.reference, .candidate {{ border:2px solid transparent; padding:8px; background:white; }}
.reference.real {{ border-color:var(--ink); }}
.reference.kai {{ border-color:#b8b3a6; background:#ece9df; }}
img {{ width:100%; aspect-ratio:1; object-fit:contain; image-rendering:auto; display:block; }}
figcaption, .candidate span {{ display:block; text-align:center; font-weight:700; padding:9px 4px 2px; }}
.reference.kai figcaption {{ color:var(--muted); }}
.candidate {{ cursor:pointer; position:relative; transition:transform .12s ease,border-color .12s ease,box-shadow .12s ease; }}
.candidate:hover {{ transform:translateY(-2px); border-color:#817d72; }}
.candidate:has(input:checked) {{ border-color:var(--accent); box-shadow:0 0 0 3px rgba(155,47,36,.16); }}
.candidate input {{ position:absolute; top:12px; left:12px; width:22px; height:22px; accent-color:var(--accent); }}
.tie {{ display:inline-flex; align-items:center; gap:9px; margin-top:14px; padding:9px 12px; cursor:pointer; border:1px solid var(--line); }}
.tie:has(input:checked) {{ border-color:var(--accent); background:#f4e7e3; }}
.tie input {{ width:19px; height:19px; accent-color:var(--accent); }}
.actions {{ position:fixed; inset:auto 0 0; background:rgba(23,23,19,.96); color:white; padding:15px max(20px,calc((100vw - 1500px)/2)); display:flex; justify-content:space-between; align-items:center; gap:20px; z-index:5; }}
.actions p {{ margin:0; }}
button {{ border:0; background:var(--accent); color:white; font-weight:800; font-size:16px; padding:13px 22px; cursor:pointer; }}
button:disabled {{ opacity:.5; cursor:not-allowed; }}
#status {{ color:#ddd8ca; }}
#result {{ display:none; background:#e6f0e3; border:1px solid #7d9675; padding:20px; margin:24px 0; font-weight:700; }}
@media (max-width:900px) {{ .comparison {{ grid-template-columns:repeat(2,1fr); }} .reference.real {{ grid-column:1; }} .reference.kai {{ grid-column:2; }} .intro {{ grid-template-columns:1fr; }} .progress {{ text-align:left; }} }}
</style>
</head>
<body>
<main>
  <div class="intro"><div><h1>Blind writer-identity review</h1><p>For each row, compare the real manuscript ink with Candidates A–C. Select the candidate that most closely preserves the same writer’s proportions, component placement, stroke behavior, rhythm, and recurring irregularities. Kai is disclosed only as the content control. Do not reward blur or damage by itself.</p></div><div class="progress"><span id="answered">0</span> / {len(key)} answered</div></div>
  <div id="result"></div>
  <form id="quiz">{''.join(rows)}</form>
</main>
<div class="actions"><p id="status">Selections save in this browser until submission.</p><button id="submit" type="button">Save blind review</button></div>
<script>
const cropIds = {crop_ids};
const storageKey = 'p3477-blind-review-v1';
const form = document.getElementById('quiz');
const answered = document.getElementById('answered');
const status = document.getElementById('status');
const submit = document.getElementById('submit');
function values() {{ const out={{}}; for (const id of cropIds) {{ const hit=form.querySelector(`input[name="${{id}}"]:checked`); if(hit) out[id]=hit.value; }} return out; }}
function refresh() {{ const current=values(); answered.textContent=Object.keys(current).length; localStorage.setItem(storageKey,JSON.stringify(current)); document.querySelectorAll('.question').forEach(q=>q.classList.remove('missing')); }}
try {{ const saved=JSON.parse(localStorage.getItem(storageKey)||'{{}}'); for(const [id,value] of Object.entries(saved)) {{ const exact=form.querySelector(`input[name="${{id}}"]:is([value="${{value}}"])`); if(exact) exact.checked=true; }} }} catch (_) {{}}
form.addEventListener('change',refresh); refresh();
submit.addEventListener('click', async () => {{
  const choices=values(); const missing=cropIds.filter(id=>!choices[id]);
  if(missing.length) {{ for(const id of missing) document.querySelector(`[data-crop="${{id}}"]`).classList.add('missing'); document.querySelector(`[data-crop="${{missing[0]}}"]`).scrollIntoView({{behavior:'smooth',block:'center'}}); status.textContent=`Answer all rows — ${{missing.length}} remaining.`; return; }}
  submit.disabled=true; status.textContent='Saving immutable review…';
  try {{ const response=await fetch('/submit',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{choices}})}}); const payload=await response.json(); if(!response.ok) throw new Error(payload.error||'Submission failed'); form.querySelectorAll('input').forEach(input=>input.disabled=true); localStorage.removeItem(storageKey); const result=document.getElementById('result'); result.style.display='block'; result.textContent=`Saved. Calibrated writer candidate won ${{payload.calibrated_wins}} of ${{payload.total}} rows; freeze gate ${{payload.passed?'PASSED':'FAILED'}}.`; result.scrollIntoView({{behavior:'smooth'}}); status.textContent='Review saved to human_blind_review.json. Return to chat and say “submitted”.'; }} catch(error) {{ submit.disabled=false; status.textContent=error.message; }}
}});
</script>
</body>
</html>"""


def adjudicate(choices: dict, key: dict) -> dict:
    if set(choices) != set(key):
        missing = sorted(set(key) - set(choices))
        extra = sorted(set(choices) - set(key))
        raise ValueError(f"Expected every blind row; missing={missing}, extra={extra}")
    invalid = {crop_id: value for crop_id, value in choices.items() if value not in CHOICES}
    if invalid:
        raise ValueError(f"Invalid choices: {invalid}")
    rows = []
    calibrated_wins = 0
    for crop_id, blind in key.items():
        choice = choices[crop_id]
        selected_system = None if choice == "Tie" else blind[choice]
        is_calibrated = selected_system == PRIMARY
        calibrated_wins += int(is_calibrated)
        rows.append(
            {
                "crop_id": crop_id,
                "character": blind["character"],
                "choice": choice,
                "selected_system": selected_system,
                "calibrated_writer_candidate_won": is_calibrated,
            }
        )
    return {
        "schema_version": 1,
        "experiment": "generative-hand-font-v1",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "report_sha256": sha256(REPORT_PATH),
        "blind_key_sha256": sha256(KEY_PATH),
        "rows": rows,
        "calibrated_wins": calibrated_wins,
        "total_rows": len(rows),
        "required_wins": REQUIRED_WINS,
        "passed": calibrated_wins >= REQUIRED_WINS,
    }


class ReviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, replace: bool = False, **kwargs):
        self.replace = replace
        super().__init__(*args, directory=str(OUT), **kwargs)

    def do_POST(self) -> None:
        if self.path != "/submit":
            self.send_error(404)
            return
        try:
            if ANSWER_PATH.exists() and not self.replace:
                raise FileExistsError("A blind review is already recorded; refusing to overwrite it")
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            key = json.loads(KEY_PATH.read_text(encoding="utf-8"))
            record = adjudicate(payload.get("choices", {}), key)
            temporary = ANSWER_PATH.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(ANSWER_PATH)
            response = {
                "saved": True,
                "calibrated_wins": record["calibrated_wins"],
                "total": record["total_rows"],
                "passed": record["passed"],
            }
            self.send_json(200, response)
        except FileExistsError as error:
            self.send_json(409, {"error": str(error)})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})

    def send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:
        print(format % args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3477)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    key = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    QUIZ_PATH.write_text(render_quiz(report, key), encoding="utf-8")
    def handler(*values, **kwargs):
        return ReviewHandler(*values, replace=args.replace, **kwargs)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{args.port}/{QUIZ_PATH.name}"
    print(f"quiz: {url}")
    print(f"answers: {ANSWER_PATH}")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
