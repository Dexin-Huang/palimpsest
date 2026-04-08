from __future__ import annotations

from html import escape


def site_css() -> str:
    return """
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --parchment: #F0EBE0;
    --parchment-deep: #E8E1D2;
    --ink: #1A1714;
    --ink-soft: #3D3630;
    --faded: #8A7E6E;
    --faded-light: #B5AA96;
    --accent: #8A4B2A;
    --rule: #D0C4AE;
    --rule-light: #E0D8C8;
    --panel-bg: #141210;
    --panel-fg: #C8BFA8;
    --glow: rgba(138,75,42,0.08);
  }
  html { font-size: 16px; }
  body {
    margin: 0;
    background: var(--panel-bg);
    color: var(--ink);
    font-family: 'Noto Serif', 'Georgia', serif;
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
    height: 100vh;
    width: 100vw;
  }
  a { color: inherit; }
  .book {
    position: fixed;
    inset: 0;
  }
  .page {
    position: absolute;
    inset: 0;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.5s ease;
  }
  .page.active {
    opacity: 1;
    pointer-events: auto;
  }
  .cover {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: var(--panel-bg);
    overflow: hidden;
  }
  .cover::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 50% 40%, rgba(138,75,42,0.07) 0%, transparent 70%);
    pointer-events: none;
  }
  .cover-label {
    font-size: 0.65rem;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 1.5rem;
    opacity: 0;
    animation: fadeUp 1.2s 0.3s ease forwards;
  }
  .cover-title {
    font-size: 2.8rem;
    font-weight: 300;
    color: var(--parchment);
    letter-spacing: 0.04em;
    opacity: 0;
    animation: fadeUp 1.2s 0.6s ease forwards;
  }
  .cover-subtitle {
    font-size: 0.85rem;
    color: var(--faded);
    margin-top: 0.75rem;
    letter-spacing: 0.15em;
    opacity: 0;
    animation: fadeUp 1.2s 0.9s ease forwards;
  }
  .cover-rule {
    width: 60px;
    height: 1px;
    background: var(--accent);
    margin-top: 2rem;
    opacity: 0;
    animation: fadeUp 1.2s 1.1s ease forwards;
  }
  .cover-nav-hint {
    position: absolute;
    bottom: 2.5rem;
    font-size: 0.6rem;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--faded-light);
    opacity: 0;
    animation: fadeUp 1.2s 1.8s ease forwards, pulse 3s 3s ease-in-out infinite;
    margin-top: 1rem;
    display: flex;
    align-items: center;
    gap: 0.8em;
  }
  .cover-nav-hint .arrow {
    font-size: 0.8rem;
    color: var(--accent);
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(16px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes pulse {
    0%, 100% { opacity: 0.4; }
    50% { opacity: 1; }
  }
  .spread-page {
    height: 100%;
  }
  .spread {
    display: grid;
    grid-template-columns: 1fr 1fr;
    background: var(--parchment);
    height: 100%;
    position: relative;
  }
  .spread::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 50%;
    width: 3px;
    background: linear-gradient(to bottom, transparent 0%, var(--rule) 5%, var(--rule) 95%, transparent 100%);
    transform: translateX(-1.5px);
    z-index: 10;
    box-shadow: -4px 0 12px rgba(0,0,0,0.03), 4px 0 12px rgba(0,0,0,0.03);
  }
  .page-image {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 1.8rem 2rem;
    background: linear-gradient(160deg, var(--parchment-deep) 0%, var(--parchment) 40%, var(--parchment-deep) 100%);
    position: relative;
    overflow: hidden;
  }
  .page-image::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at 55% 45%, var(--glow), transparent 65%);
    pointer-events: none;
  }
  .image-header {
    position: absolute;
    top: 1.2rem;
    left: 1.5rem;
    right: 1.5rem;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }
  .image-header-folio {
    font-size: 0.6rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded);
  }
  .image-header-source {
    font-size: 0.55rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .image-frame {
    position: relative;
    max-width: 90%;
    max-height: 85vh;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06), 0 12px 32px rgba(0,0,0,0.08), 0 32px 72px rgba(0,0,0,0.05);
    line-height: 0;
    border-radius: 1px;
    overflow: hidden;
    background: rgba(255,255,255,0.35);
  }
  .image-frame img {
    display: block;
    width: 100%;
    height: auto;
    max-height: 85vh;
    object-fit: contain;
  }
  .image-frame::after {
    content: '';
    position: absolute;
    inset: 0;
    border: 1px solid rgba(0,0,0,0.05);
    pointer-events: none;
  }
  .image-overlay {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  .image-region {
    position: absolute;
    border: 2px solid rgba(138,75,42,0.45);
    background: rgba(138,75,42,0.08);
    box-shadow: inset 0 0 0 1px rgba(247,241,230,0.22);
    transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
    pointer-events: none;
    cursor: pointer;
  }
  .image-region:hover,
  .image-region.is-linked-active {
    border-color: rgba(138,75,42,0.95);
    background: rgba(138,75,42,0.18);
    box-shadow: inset 0 0 0 1px rgba(247,241,230,0.5), 0 0 0 2px rgba(138,75,42,0.18);
  }
  .image-region-label {
    position: absolute;
    top: -1.2rem;
    left: 0;
    background: rgba(20,18,16,0.86);
    color: var(--parchment);
    font-size: 0.46rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    line-height: 1;
    padding: 0.22rem 0.32rem;
    white-space: nowrap;
    opacity: 0;
    transform: translateY(2px);
    transition: opacity 0.18s ease, transform 0.18s ease;
  }
  .image-region:hover .image-region-label,
  .image-region.is-linked-active .image-region-label {
    opacity: 1;
    transform: translateY(0);
  }
  .image-caption {
    position: absolute;
    bottom: 1rem;
    font-size: 0.5rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .right-panel {
    position: relative;
    overflow: hidden;
  }
  .face {
    position: absolute;
    inset: 0;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 2rem 2.5rem 3rem 2rem;
    transition: opacity 0.4s ease, transform 0.4s ease;
    scrollbar-width: thin;
    scrollbar-color: var(--rule) transparent;
  }
  .face::-webkit-scrollbar { width: 5px; }
  .face::-webkit-scrollbar-track { background: transparent; }
  .face::-webkit-scrollbar-thumb { background: var(--rule); border-radius: 3px; }
  .face-witness {
    background: var(--parchment);
    box-shadow: inset 6px 0 16px -8px rgba(0,0,0,0.04);
  }
  .face-interp {
    background: var(--panel-bg);
    opacity: 0;
    pointer-events: none;
    transform: translateY(8px);
  }
  .right-panel.flipped .face-witness {
    opacity: 0;
    pointer-events: none;
    transform: translateY(-8px);
  }
  .right-panel.flipped .face-interp {
    opacity: 1;
    pointer-events: auto;
    transform: translateY(0);
  }
  .page-text-inner { max-width: 500px; }
  .page-header {
    margin-bottom: 1.6rem;
    padding-bottom: 0.8rem;
    border-bottom: 1px solid var(--rule);
  }
  .page-header-dark {
    border-color: rgba(200,191,168,0.15);
  }
  .page-header-label {
    font-size: 0.5rem;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.2rem;
  }
  .page-header-title {
    font-size: 1rem;
    font-weight: 400;
    color: var(--ink);
    letter-spacing: 0.02em;
  }
  .page-header-dark .page-header-title {
    color: var(--panel-fg);
  }
  .content-block {
    margin-top: 2rem;
  }
  .content-block:first-of-type {
    margin-top: 0;
  }
  .content-block-title,
  .apparatus-block-title {
    font-size: 0.55rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.9rem;
  }
  .apparatus-block {
    margin-top: 1.8rem;
  }
  .apparatus-block:first-of-type {
    margin-top: 0;
  }
  .apparatus-block-wide {
    margin-top: 2rem;
  }
  .section-card {
    margin-top: 0.85rem;
    padding-top: 0.85rem;
    border-top: 1px solid var(--rule-light);
  }
  .section-card:first-child {
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
  }
  .section-card-title {
    font-size: 1rem;
    color: var(--ink);
    margin-bottom: 0.5rem;
  }
  .section-card-body p, .apparatus-card-body p {
    margin: 0 0 0.7rem 0;
    line-height: 1.72;
    color: var(--ink-soft);
  }
  .section-card-body ul, .section-card-body ol,
  .apparatus-card-body ul, .apparatus-card-body ol {
    margin: 0 0 0.8rem 1.2rem;
    padding: 0;
    line-height: 1.68;
    color: var(--ink-soft);
  }
  .section-card-body li, .apparatus-card-body li { margin-bottom: 0.35rem; }
  .section-card-body pre, .apparatus-card-body pre {
    margin: 0.8rem 0;
    padding: 0.9rem 1rem;
    background: #efe7d8;
    border-left: 3px solid var(--accent);
    overflow-x: auto;
    font-size: 0.9rem;
    line-height: 1.55;
    color: var(--ink);
    white-space: pre-wrap;
  }
  .section-card-body hr, .apparatus-card-body hr {
    border: 0;
    height: 1px;
    background: var(--rule);
    margin: 1rem 0;
  }
  .subsection-card {
    margin-top: 0.9rem;
    padding: 0.8rem 0 0 0.9rem;
    border-left: 2px solid var(--rule);
  }
  .subsection-card-title {
    font-size: 0.7rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
    margin-bottom: 0.45rem;
  }
  .subsection-card-body p,
  .subsection-card-body ul,
  .subsection-card-body ol,
  .subsection-card-body li {
    color: var(--ink-soft);
    line-height: 1.68;
  }
  .content-block--witness .content-block-title {
    color: var(--accent);
  }
  .content-block--translation .content-block-title {
    color: var(--faded);
  }
  .apparatus-block--interpretation .apparatus-block-title,
  .apparatus-block--questions .apparatus-block-title {
    color: var(--parchment);
  }
  .section-card-body code, .apparatus-card-body code {
    background: rgba(20,18,16,0.06);
    padding: 0.08rem 0.28rem;
    border-radius: 2px;
    font-size: 0.95em;
  }
  .face-interp { color: var(--panel-fg); }
  .face-interp p,
  .face-interp ul,
  .face-interp ol,
  .face-interp li {
    color: var(--faded-light);
  }
  .face-interp strong { color: var(--panel-fg); }
  .face-interp em { color: var(--faded-light); }
  .face-interp pre {
    background: rgba(240,235,224,0.06);
    border-left-color: var(--accent);
    color: var(--faded-light);
  }
  .face-interp code {
    background: rgba(240,235,224,0.08);
    color: var(--panel-fg);
  }
  .face-interp hr {
    background: rgba(200,191,168,0.12);
  }
  .face-interp .section-card {
    border-top-color: rgba(200,191,168,0.12);
  }
  .face-interp .section-card-title {
    color: var(--panel-fg);
  }
  .face-interp .subsection-card {
    border-left-color: rgba(200,191,168,0.16);
  }
  .face-interp .subsection-card-title {
    color: var(--parchment);
  }
  .column-header { margin-top: 2rem; margin-bottom: 1.2rem; }
  .column-header:first-child { margin-top: 0; }
  .witness-unit {
    position: relative;
    transition: background 0.18s ease, box-shadow 0.18s ease;
    border-radius: 4px;
  }
  .witness-unit.is-linked-active {
    background: rgba(138,75,42,0.08);
    box-shadow: 0 0 0 1px rgba(138,75,42,0.16);
  }
  .column-header-chinese {
    font-size: 1.1rem;
    color: var(--accent);
    letter-spacing: 0.08em;
    margin-bottom: 0.15rem;
  }
  .column-header-english {
    font-size: 0.55rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
  }
  .column-rule {
    width: 36px;
    height: 2px;
    background: var(--accent);
    margin-top: 0.5rem;
    border-radius: 1px;
  }
  .pair {
    padding: 0.65rem 0;
    border-bottom: 1px solid var(--rule-light);
    transition: background 0.25s ease;
  }
  .pair:hover,
  .pair.is-linked-active {
    background: var(--glow);
    margin-left: -0.6rem; margin-right: -0.6rem;
    padding-left: 0.6rem; padding-right: 0.6rem;
    border-radius: 2px;
  }
  .pair:last-of-type { border-bottom: none; }
  .pair-source {
    font-size: 0.95rem;
    line-height: 1.7;
    color: var(--ink);
    margin-bottom: 0.2rem;
  }
  .pair-translation {
    font-size: 0.76rem;
    line-height: 1.6;
    color: var(--faded);
    font-style: italic;
  }
  .pair-translation em { font-style: normal; color: var(--ink-soft); }
  .pair-translation .uncertain {
    color: var(--accent);
    font-style: normal;
    font-size: 0.68rem;
  }
  .lacuna { color: var(--accent); font-weight: 600; letter-spacing: 0.04em; }
  .marginalia-section {
    margin-top: 1.8rem;
    padding-top: 1.2rem;
    border-top: 1px solid var(--rule);
  }
  .marginalia-label {
    font-size: 0.5rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.6rem;
  }
  .marginalia-text {
    font-size: 0.72rem;
    line-height: 1.75;
    color: var(--faded);
    font-style: italic;
  }
  .marginalia-note {
    margin-top: 0.4rem;
    font-size: 0.65rem;
    line-height: 1.55;
    color: var(--faded-light);
    font-style: normal;
  }
  .interpretation-label {
    font-size: 0.5rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.8rem;
  }
  .interpretation-text {
    font-size: 0.74rem;
    line-height: 1.75;
    color: var(--panel-fg);
  }
  .interpretation-text + .interpretation-text { margin-top: 0.7rem; }
  .terms-divider {
    margin-top: 1.2rem;
    padding-top: 1rem;
    border-top: 1px solid rgba(200,191,168,0.12);
  }
  .terms-label {
    font-size: 0.45rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--faded);
    margin-bottom: 0.6rem;
  }
  .term-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.3rem 1.5rem;
  }
  .term { font-size: 0.65rem; line-height: 1.45; color: var(--faded-light); }
  .term-zh { color: var(--panel-fg); margin-right: 0.2em; }
  .colophon {
    margin-top: 1.5rem;
    text-align: center;
    padding: 0.8rem 0 0.5rem;
  }
  .colophon-text {
    font-size: 0.45rem;
    letter-spacing: 0.28em;
    text-transform: uppercase;
    color: var(--faded-light);
  }
  .flip-symbol {
    position: absolute;
    top: 1.1rem;
    right: 1.2rem;
    z-index: 20;
    width: 18px;
    height: 18px;
    cursor: pointer;
    user-select: none;
    opacity: 0.35;
    transition: opacity 0.3s ease, transform 0.4s ease;
    background: transparent;
    border: none;
    padding: 0;
  }
  .flip-symbol:hover {
    opacity: 0.7;
  }
  .flip-symbol svg {
    width: 100%;
    height: 100%;
    transition: transform 0.4s ease;
  }
  .folio-links {
    position: fixed;
    right: 1.2rem;
    bottom: 1.2rem;
    display: flex;
    gap: 0.7rem;
    z-index: 100;
  }
  .folio-link {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    text-decoration: none;
    padding: 0.65rem 0.8rem;
    border-radius: 999px;
    background: rgba(20,18,16,0.85);
    color: var(--faded-light);
    font-size: 0.62rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }
  .folio-link:hover {
    color: var(--parchment);
  }
  @media (max-width: 1120px) {
    .spread {
      grid-template-columns: 1fr;
    }
    .spread::after {
      display: none;
    }
    .page-image {
      height: 50vh;
      padding: 1.5rem;
    }
  }
  .empty-note {
    margin: 0;
    color: var(--faded-light);
    line-height: 1.7;
  }
"""


def html_shell(*, title: str, body: str, extra_css: str = "") -> str:
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="UTF-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            f"  <title>{escape(title)}</title>",
            "  <style>",
            site_css(),
            extra_css,
            "  </style>",
            "</head>",
            "<body>",
            body,
            "</body>",
            "</html>",
        ]
    )
