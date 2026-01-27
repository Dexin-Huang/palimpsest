/**
 * Render a PPTX “editable facsimile” from a Rescript page JSON.
 *
 * Each page becomes a slide:
 * - slide background = scan image
 * - each zone = a text box placed using bbox_norm
 *
 * Usage:
 *   node node/render_pptx.mjs \
 *     --page projects/pasajeros_a_indias/pages/pares_pasajeros_demo_p0001.page.json \
 *     --out  projects/pasajeros_a_indias/exports/pptx/demo.pptx \
 *     --layer es_normalized
 */
import fs from "fs";
import path from "path";
import PptxGenJS from "pptxgenjs";

function parseArgs() {
  const args = process.argv.slice(2);
  const out = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--page") out.page = args[++i];
    else if (a === "--out") out.out = args[++i];
    else if (a === "--layer") out.layer = args[++i];
  }
  if (!out.page || !out.out) {
    console.error("Missing --page or --out");
    process.exit(2);
  }
  out.layer = out.layer || "es_normalized";
  return out;
}

// Map normalized bbox to slide inches
function bboxToInches(bbox, slideW, slideH) {
  const [x, y, w, h] = bbox;
  return { x: x * slideW, y: y * slideH, w: w * slideW, h: h * slideH };
}

async function main() {
  const { page: pagePath, out, layer } = parseArgs();
  const page = JSON.parse(fs.readFileSync(pagePath, "utf-8"));

  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE"; // 13.333 x 7.5 in
  const slideW = 13.333;
  const slideH = 7.5;

  const slide = pptx.addSlide();

  // Resolve image path relative to repo root
  const imgRel = page.image.path.replace(/^\/+/, "");
  const imgAbs = path.resolve(process.cwd(), imgRel);
  slide.addImage({ path: imgAbs, x: 0, y: 0, w: slideW, h: slideH });

  for (const z of (page.zones || [])) {
    const txt = (z.text && z.text[layer]) ? z.text[layer] : "";
    if (!txt) continue;

    const { x, y, w, h } = bboxToInches(z.bbox_norm, slideW, slideH);

    // For v1: use a small font; users can later adjust.
    slide.addText(txt, {
      x, y, w, h,
      fontFace: "Calibri",
      fontSize: 10,
      valign: "top",
      margin: 0
    });
  }

  await pptx.writeFile({ fileName: out });
  console.log("[OK] wrote", out);
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});