/* mnemosyne_ui/static/avatar.js
 *
 * Pure SVG avatar rendered into an existing <div id="avatar-stage">.
 * No frameworks. Reads a state dict from the /avatar endpoint and
 * draws/animates accordingly.
 *
 * Visual elements (each maps to one observable agent trait — see
 * mnemosyne_avatar.py):
 *
 *   core      — central orb. Color = palette.core, brightness = health.
 *   aura      — soft outer glow. Radius = state.aura_radius.
 *               Pulse rate = state.pulses_per_minute.
 *   rings     — concentric thin circles. Count = state.rings (inner-
 *               dialogue activations, capped 8).
 *   orbiters  — small dots circling the core. One per learned skill;
 *               speed proportional to recent activity.
 *   scars     — small dim arc segments on the rim. One per identity
 *               slip. Visible reminder of past failures; fade with
 *               additional time.
 *   roots     — three downward-extending lines representing L1/L2/L3
 *               memory tier counts.
 *   eye       — opens wider on focus, narrows on rest.
 *
 * Mood phase changes the animation ambience:
 *   rest        — slow breathing, dim aura
 *   focus       — sharp eye, slight forward lean (tilt animation)
 *   explore     — orbiters speed up, hue shifts subtly
 *   consolidate — petal-like inward shimmer (dream halo)
 */

"use strict";

const NS = "http://www.w3.org/2000/svg";
const AVATAR_VIEWBOX = 500;

function el(name, attrs) {
  const node = document.createElementNS(NS, name);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      node.setAttribute(k, v);
    }
  }
  return node;
}

function clip(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

function setCssVars(palette) {
  const r = document.documentElement.style;
  r.setProperty("--core",   palette.core);
  r.setProperty("--accent", palette.accent);
  r.setProperty("--rim",    palette.rim);
}

function buildSvg(state) {
  const svg = el("svg", {
    viewBox: `0 0 ${AVATAR_VIEWBOX} ${AVATAR_VIEWBOX}`,
    xmlns: NS,
    role: "img",
    "aria-label":
      `Mnemosyne avatar — mood ${state.mood_phase}, ` +
      `health ${(state.health * 100).toFixed(0)}%`,
  });

  const cx = AVATAR_VIEWBOX / 2;
  const cy = AVATAR_VIEWBOX / 2 + 10;
  const palette = state.palette;

  // ---- background gradient defs ----
  const defs = el("defs");
  const auraGrad = el("radialGradient", {
    id: "auraGrad", cx: "50%", cy: "50%", r: "60%",
  });
  auraGrad.appendChild(el("stop", { offset: "0%",
    "stop-color": palette.core, "stop-opacity": "0.55" }));
  auraGrad.appendChild(el("stop", { offset: "55%",
    "stop-color": palette.core, "stop-opacity": "0.18" }));
  auraGrad.appendChild(el("stop", { offset: "100%",
    "stop-color": palette.core, "stop-opacity": "0" }));
  defs.appendChild(auraGrad);

  const coreGrad = el("radialGradient", {
    id: "coreGrad", cx: "50%", cy: "45%", r: "60%",
  });
  coreGrad.appendChild(el("stop", { offset: "0%",
    "stop-color": palette.rim, "stop-opacity": "1" }));
  coreGrad.appendChild(el("stop", { offset: "55%",
    "stop-color": palette.core, "stop-opacity": "1" }));
  coreGrad.appendChild(el("stop", { offset: "100%",
    "stop-color": palette.bg, "stop-opacity": "1" }));
  defs.appendChild(coreGrad);

  svg.appendChild(defs);

  // ---- aura ring (the breathing halo) ----
  const aura = el("circle", {
    cx, cy, r: state.aura_radius * 1.55,
    fill: "url(#auraGrad)",
    opacity: 0.85,
  });
  // Pulse via SMIL — supported in every modern browser.
  const pulseSec = clip(60 / Math.max(1, state.pulses_per_minute), 0.6, 6);
  aura.appendChild(el("animate", {
    attributeName: "opacity",
    values: "0.35;0.95;0.35",
    dur: `${pulseSec.toFixed(2)}s`,
    repeatCount: "indefinite",
  }));
  aura.appendChild(el("animateTransform", {
    attributeName: "transform",
    type: "scale",
    values: "0.96;1.04;0.96",
    additive: "sum",
    dur: `${pulseSec.toFixed(2)}s`,
    repeatCount: "indefinite",
  }));
  svg.appendChild(aura);

  // ---- inner-dialogue rings ----
  for (let i = 0; i < state.rings; i++) {
    const r = state.aura_radius * (0.65 + i * 0.12);
    const ring = el("circle", {
      cx, cy, r,
      fill: "none",
      stroke: palette.accent,
      "stroke-opacity": 0.18 + 0.06 * (state.rings - i),
      "stroke-width": 1.2,
    });
    ring.appendChild(el("animate", {
      attributeName: "stroke-opacity",
      values: "0.35;0.05;0.35",
      dur: `${(2 + i * 0.5).toFixed(2)}s`,
      repeatCount: "indefinite",
    }));
    svg.appendChild(ring);
  }

  // ---- core orb ----
  const core = el("circle", {
    cx, cy, r: 56 + state.health * 12,
    fill: "url(#coreGrad)",
    stroke: palette.rim,
    "stroke-opacity": 0.55,
    "stroke-width": 1.5,
  });
  svg.appendChild(core);

  // ---- eye (mood-aware) ----
  const eyeOpen = state.mood_phase === "focus"
                ? 16
                : state.mood_phase === "rest"
                  ? 4
                  : 10;
  const eye = el("ellipse", {
    cx, cy: cy - 4, rx: 18, ry: eyeOpen,
    fill: palette.bg,
    stroke: palette.rim, "stroke-width": 1,
  });
  svg.appendChild(eye);
  const pupil = el("circle", {
    cx, cy: cy - 4, r: 5,
    fill: palette.accent,
    opacity: 0.95,
  });
  svg.appendChild(pupil);

  // ---- orbiters (one per learned skill, capped 12) ----
  const orbiterCount = clip(state.skills_count, 0, 12);
  const orbitR = state.aura_radius + 28;
  const orbitSpeed = clip(20 - state.activity_score * 14, 4, 20);
  const orbitGroup = el("g");
  for (let i = 0; i < orbiterCount; i++) {
    const a = (i / orbiterCount) * Math.PI * 2;
    const dot = el("circle", {
      cx: cx + orbitR * Math.cos(a),
      cy: cy + orbitR * Math.sin(a),
      r: 3,
      fill: palette.accent,
      opacity: 0.78,
    });
    orbitGroup.appendChild(dot);
  }
  // Rotate the whole group continuously
  const rot = el("animateTransform", {
    attributeName: "transform",
    type: "rotate",
    from: `0 ${cx} ${cy}`,
    to: `360 ${cx} ${cy}`,
    dur: `${orbitSpeed.toFixed(1)}s`,
    repeatCount: "indefinite",
  });
  orbitGroup.appendChild(rot);
  svg.appendChild(orbitGroup);

  // ---- scars (one short arc per identity slip, max 12) ----
  const scarCount = clip(state.identity_slip_count, 0, 12);
  for (let i = 0; i < scarCount; i++) {
    const a = (i / Math.max(1, scarCount)) * Math.PI * 2;
    const r1 = 60 + state.health * 12 + 4;
    const r2 = r1 + 4;
    const x1 = cx + r1 * Math.cos(a);
    const y1 = cy + r1 * Math.sin(a);
    const x2 = cx + r2 * Math.cos(a + 0.16);
    const y2 = cy + r2 * Math.sin(a + 0.16);
    svg.appendChild(el("line", {
      x1, y1, x2, y2,
      stroke: "#f25c6f",
      "stroke-opacity": 0.45,
      "stroke-width": 1.6,
      "stroke-linecap": "round",
    }));
  }

  // ---- memory roots (three downward lines, length proportional to tier counts) ----
  const tiers = [
    { count: state.l1_count, color: palette.accent, x: cx - 20 },
    { count: state.l2_count, color: palette.core,   x: cx },
    { count: state.l3_count, color: palette.rim,    x: cx + 20 },
  ];
  const rootBase = cy + (60 + state.health * 12) - 4;
  const maxRoot = 110;
  for (const t of tiers) {
    const len = clip(20 + Math.log10(1 + t.count) * 30, 20, maxRoot);
    const root = el("line", {
      x1: t.x, y1: rootBase,
      x2: t.x, y2: rootBase + len,
      stroke: t.color,
      "stroke-opacity": 0.55,
      "stroke-width": 2,
      "stroke-linecap": "round",
    });
    svg.appendChild(root);
  }

  // ---- consolidate-mode petals (only on dream cadence) ----
  if (state.mood_phase === "consolidate" && state.dreams_count > 0) {
    const petalCount = 6;
    for (let i = 0; i < petalCount; i++) {
      const a = (i / petalCount) * Math.PI * 2;
      const r1 = state.aura_radius * 1.1;
      const px = cx + r1 * Math.cos(a);
      const py = cy + r1 * Math.sin(a);
      const petal = el("circle", {
        cx: px, cy: py, r: 6,
        fill: palette.accent, opacity: 0.4,
      });
      petal.appendChild(el("animate", {
        attributeName: "r",
        values: "3;9;3",
        dur: `${(3 + i * 0.2).toFixed(2)}s`,
        repeatCount: "indefinite",
      }));
      svg.appendChild(petal);
    }
  }

  return svg;
}

function renderTraitGrid(state, container) {
  const traits = [
    ["mood",            state.mood_phase],
    ["age (days)",      state.age_days.toFixed(1)],
    ["memories",        state.memory_count],
    ["skills",          state.skills_count + (state.learned_skills
                          ? ` (+${state.learned_skills} learned)` : "")],
    ["goals (open)",    state.goals_open],
    ["goals resolved",  state.goals_resolved],
    ["dreams",          state.dreams_count],
    ["inner dialogues", state.inner_dialogues],
    ["identity",        (state.identity_strength * 100).toFixed(1) + "%"],
    ["activity",        (state.activity_score * 100).toFixed(0) + "%"],
    ["health",          (state.health * 100).toFixed(0) + "%"],
    ["pulse",           state.pulses_per_minute + " bpm"],
  ];
  container.innerHTML = "";
  for (const [k, v] of traits) {
    const div = document.createElement("div");
    div.className = "trait";
    div.innerHTML = `<span class="k">${k}</span><span class="v">${v}</span>`;
    container.appendChild(div);
  }
}

function renderAvatar(state) {
  setCssVars(state.palette);
  const stage = document.getElementById("avatar-stage");
  if (!stage) return;
  stage.innerHTML = "";
  stage.appendChild(buildSvg(state));
  const tg = document.getElementById("trait-grid");
  if (tg) renderTraitGrid(state, tg);
  const hint = document.getElementById("avatar-mood-hint");
  if (hint) hint.textContent = state.mood_phase;
}

window.MnemoAvatar = { render: renderAvatar };
