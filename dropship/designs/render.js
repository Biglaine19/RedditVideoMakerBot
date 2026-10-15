// Renders the houseplant design set to print-ready transparent PNGs.
//   node dropship/designs/render.js
// Art is original SVG; the font is Fraunces (SIL Open Font License, free for
// commercial print). Sizes follow Printify print areas (catalog, 2026-10-15).
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const OUT = path.join(__dirname, "png");
const G = "#2F4A3A", T = "#C0643F", S = "#8FA88A", C = "#E9DFC9";
const FONT = "https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400..900;1,9..144,400..900&display=block";

// --- artwork, each drawn in a 1000 x 1200 box ---------------------------
function monsteraLeaf(cx, cy, s, id) {
  const slits = [];
  for (const side of [-1, 1]) {
    for (let i = 0; i < 5; i++) {
      const y = 250 + i * 80;
      const start = [110, 80, 70, 95, 120][i];
      slits.push(`<line x1="${500 + side * start}" y1="${y + 20}" x2="${500 + side * 460}" y2="${y - 110 + i * 45}" stroke="black" stroke-width="${[16, 22, 24, 22, 18][i]}" stroke-linecap="round"/>`);
      if (i < 4) slits.push(`<ellipse cx="${500 + side * 150}" cy="${y + 10}" rx="20" ry="11" transform="rotate(${side * -20} ${500 + side * 150} ${y + 10})" fill="black"/>`);
    }
  }
  return `<g transform="translate(${cx - 500 * s} ${cy - 400 * s}) scale(${s})">
    <mask id="${id}"><rect x="0" y="0" width="1000" height="1000" fill="white"/>
      ${slits.join("")}
      <line x1="500" y1="640" x2="500" y2="175" stroke="black" stroke-width="10" stroke-linecap="round"/></mask>
    <path mask="url(#${id})" fill="${G}" d="M500,640 C470,690 400,720 330,700 C200,660 140,520 160,400 C185,240 330,140 500,130 C670,140 815,240 840,400 C860,520 800,660 670,700 C600,720 530,690 500,640 Z"/>
  </g>`;
}

function pot(cx, top, w) {
  const h = w * 0.75;
  return `<path fill="${T}" d="M${cx - w / 2 + w * 0.08},${top + w * 0.18} L${cx + w / 2 - w * 0.08},${top + w * 0.18} L${cx + w * 0.36},${top + h} L${cx - w * 0.36},${top + h} Z"/>
    <rect fill="${T}" x="${cx - w / 2}" y="${top}" width="${w}" height="${w * 0.16}" rx="${w * 0.03}"/>
    <line x1="${cx - w * 0.3}" y1="${top + w * 0.4}" x2="${cx + w * 0.3}" y2="${top + w * 0.4}" stroke="${C}" stroke-width="${w * 0.03}" stroke-linecap="round"/>`;
}

const title = (y, size, text, fill = G, extra = "") =>
  `<text x="500" y="${y}" text-anchor="middle" font-family="Fraunces" font-weight="800" font-size="${size}" letter-spacing="${size * 0.04}" fill="${fill}" ${extra}>${text}</text>`;

const ART = {
  "rooted-and-thriving": `
    ${monsteraLeaf(500, 430, 0.95, "mA")}
    <path d="M500,690 C495,740 505,780 500,830" stroke="${G}" stroke-width="22" fill="none" stroke-linecap="round"/>
    ${pot(500, 815, 250)}
    ${title(1085, 84, "ROOTED &amp; THRIVING")}
    <text x="500" y="1160" text-anchor="middle" font-family="Fraunces" font-style="italic" font-weight="500" font-size="54" fill="${S}">grow at your own pace</text>`,

  "photosynthesis-club": (() => {
    const rays = Array.from({ length: 12 }, (_, i) => {
      const a = (i * 30 * Math.PI) / 180;
      return `<line x1="${500 + Math.cos(a) * 72}" y1="${470 + Math.sin(a) * 72}" x2="${500 + Math.cos(a) * 102}" y2="${470 + Math.sin(a) * 102}" stroke="${T}" stroke-width="12" stroke-linecap="round"/>`;
    }).join("");
    return `
    <defs><path id="arcTop" d="M190,600 A310,310 0 0 1 810,600"/><path id="arcBot" d="M160,600 A340,340 0 0 0 840,600"/></defs>
    <circle cx="500" cy="600" r="420" fill="none" stroke="${G}" stroke-width="16"/>
    <circle cx="500" cy="600" r="250" fill="none" stroke="${G}" stroke-width="5"/>
    <text font-family="Fraunces" font-weight="800" font-size="78" letter-spacing="6" fill="${G}"><textPath href="#arcTop" startOffset="50%" text-anchor="middle">PHOTOSYNTHESIS</textPath></text>
    <text font-family="Fraunces" font-weight="700" font-size="64" letter-spacing="10" fill="${G}" dominant-baseline="hanging"><textPath href="#arcBot" startOffset="50%" text-anchor="middle">★ CLUB ★</textPath></text>
    <circle cx="500" cy="470" r="54" fill="${T}"/>${rays}
    <path d="M500,800 C500,750 495,700 500,660" stroke="${G}" stroke-width="16" fill="none" stroke-linecap="round"/>
    <path d="M500,730 C440,738 405,700 395,660 C450,652 495,685 500,730 Z" fill="${S}"/>
    <path d="M500,705 C560,712 605,672 612,625 C552,620 505,655 500,705 Z" fill="${G}"/>
    <path d="M370,800 Q500,770 630,800" stroke="${G}" stroke-width="12" fill="none" stroke-linecap="round"/>
    <text x="500" y="1110" text-anchor="middle" font-family="Fraunces" font-style="italic" font-weight="500" font-size="54" fill="${S}">members soak up the good stuff</text>`;
  })(),

  "propagation-station": (() => {
    const jar = (x, h, leaves, tilt) => {
      const top = 820 - h, w = 150;
      const roots = [0, 1, 2].map(i => `<path d="M${x + (i - 1) * 6},${top + h * 0.45} q ${-20 + i * 20},40 ${-10 + i * 12},${h * 0.4}" stroke="${T}" stroke-width="5" fill="none" stroke-linecap="round"/>`).join("");
      const lv = leaves.map(([dx, dy, r]) => `<path transform="translate(${x + dx} ${top - 60 + dy}) rotate(${r})" d="M0,0 C-40,-10 -55,-55 -30,-80 C-15,-95 0,-85 0,-72 C0,-85 15,-95 30,-80 C55,-55 40,-10 0,0 Z" fill="${G}"/>`).join("");
      return `<clipPath id="j${x}"><rect x="${x - w / 2}" y="${top}" width="${w}" height="${h}" rx="30"/></clipPath>
        <rect clip-path="url(#j${x})" x="${x - w / 2}" y="${top + h * 0.35}" width="${w}" height="${h}" fill="${S}" opacity="0.85"/>
        ${roots}
        <path d="M${x},${top + h * 0.5} C${x + tilt},${top} ${x - tilt},${top - 30} ${x},${top - 60}" stroke="${G}" stroke-width="9" fill="none" stroke-linecap="round"/>
        <rect x="${x - w / 2}" y="${top}" width="${w}" height="${h}" rx="30" fill="none" stroke="${G}" stroke-width="10"/>
        <rect x="${x - w / 2 - 12}" y="${top - 8}" width="${w + 24}" height="26" rx="10" fill="${G}"/>
        ${lv}`;
    };
    return `
    ${title(230, 116, "PROPAGATION")}
    ${jar(300, 300, [[0, 0, -15], [35, 40, 40]], 25)}
    ${jar(500, 380, [[0, 0, 5], [-40, 50, -45], [40, 70, 50]], -20)}
    ${jar(700, 260, [[0, 0, 20], [-35, 45, -35]], 20)}
    <line x1="170" y1="840" x2="830" y2="840" stroke="${G}" stroke-width="12" stroke-linecap="round"/>
    ${title(970, 116, "STATION")}
    <text x="500" y="1060" text-anchor="middle" font-family="Fraunces" font-style="italic" font-weight="500" font-size="54" fill="${S}">one cutting at a time</text>`;
  })(),

  "overwatering-love-language": (() => {
    const heart = (x, y, s) => `<path transform="translate(${x} ${y}) scale(${s})" d="M0,30 C-50,-5 -40,-45 -12,-40 C-4,-38 0,-30 0,-24 C0,-30 4,-38 12,-40 C40,-45 50,-5 0,30 Z" fill="${T}"/>`;
    return `
    <g transform="rotate(-18 480 380)">
      <rect x="300" y="250" width="330" height="260" rx="40" fill="${G}"/>
      <path d="M630,330 L840,210" stroke="${G}" stroke-width="34" stroke-linecap="round"/>
      <rect x="815" y="180" width="70" height="40" rx="12" transform="rotate(-30 850 200)" fill="${G}"/>
      <path d="M330,250 C330,120 600,120 600,250" stroke="${G}" stroke-width="28" fill="none"/>
      <path d="M300,330 C210,330 210,450 300,450" stroke="${G}" stroke-width="28" fill="none"/>
      <path d="M340,380 L590,380" stroke="${C}" stroke-width="14" stroke-linecap="round"/>
    </g>
    ${heart(860, 360, 1.3)}${heart(800, 470, 1.0)}${heart(880, 560, 0.8)}${heart(820, 640, 0.6)}
    ${title(820, 112, "OVERWATERING")}
    <text x="500" y="930" text-anchor="middle" font-family="Fraunces" font-style="italic" font-weight="600" font-size="86" fill="${G}">is my love language</text>
    <text x="500" y="1040" text-anchor="middle" font-family="Fraunces" font-weight="600" font-size="44" letter-spacing="8" fill="${S}">(SORRY, FERN)</text>`;
  })(),
};

// --- formats ---------------------------------------------------------------
const STICKER_OUTLINE = `<filter id="die" x="-10%" y="-10%" width="120%" height="120%">
  <feMorphology in="SourceAlpha" operator="dilate" radius="14"/>
  <feGaussianBlur stdDeviation="14"/>
  <feComponentTransfer result="d"><feFuncA type="linear" slope="12" intercept="-1"/></feComponentTransfer>
  <feFlood flood-color="#fff"/><feComposite in2="d" operator="in" result="w"/>
  <feMerge><feMergeNode in="w"/><feMergeNode in="SourceGraphic"/></feMerge></filter>`;

const FORMATS = {
  tee: { w: 1500, h: 1800, scale: 3, svg: a => `<svg viewBox="0 0 1000 1200" width="1500" height="1800">${a}</svg>` },
  sticker: { w: 832, h: 832, scale: 2, svg: a => `<svg viewBox="-60 -40 1120 1280" width="832" height="832" preserveAspectRatio="xMidYMid meet"><defs>${STICKER_OUTLINE}</defs><g filter="url(#die)">${a}</g></svg>` },
  mug11oz: { w: 825, h: 385, scale: 3, svg: a => `<svg viewBox="0 0 2475 1155" width="825" height="385">
    <svg x="150" y="40" width="900" height="1075" viewBox="0 0 1000 1200">${a}</svg>
    <svg x="1425" y="40" width="900" height="1075" viewBox="0 0 1000 1200">${a}</svg></svg>` },
};

const page = (body, w, h, bg = "transparent") => `<!doctype html><html><head><link rel="stylesheet" href="${FONT}">
  <style>html,body{margin:0;background:${bg}}body{width:${w}px;height:${h}px;overflow:hidden}svg{display:block}</style></head>
  <body>${body}</body></html>`;

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  async function shot(html, w, h, scale, file, transparent = true) {
    const p = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: scale });
    await p.setContent(html, { waitUntil: "networkidle" });
    await p.evaluate(() => document.fonts.ready);
    await p.screenshot({ path: file, omitBackground: transparent });
    await p.close();
  }
  for (const [name, art] of Object.entries(ART)) {
    for (const [fmt, f] of Object.entries(FORMATS)) {
      await shot(page(f.svg(art), f.w, f.h), f.w, f.h, f.scale, path.join(OUT, `${name}_${fmt}.png`));
    }
  }
  // Preview sheet: each design on two common shirt colours (not for print).
  const tiles = Object.values(ART).map(a => ["#F3EEE3", "#B9C7B4"].map(bg =>
    `<div style="background:${bg};padding:20px;border-radius:12px"><svg viewBox="0 0 1000 1200" width="260" height="312">${a}</svg></div>`).join("")).join("");
  const sheet = `<div style="display:grid;grid-template-columns:repeat(4,300px);gap:16px;padding:16px;background:#fff">${tiles}</div>`;
  await shot(page(sheet, 1264, 752, "#fff"), 1264, 752, 1, path.join(__dirname, "preview.png"), false);
  await browser.close();
  console.log("rendered", fs.readdirSync(OUT).length, "files");
})();
