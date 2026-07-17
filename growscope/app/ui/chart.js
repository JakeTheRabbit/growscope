/* GrowScope chart engine. Canvas, no deps. Multi-series with per-unit axes,
   crosshair, drag-zoom, recipe target step-lines, phase bands, journal pins,
   and an external playhead for replay sync. */
"use strict";

const GSChart = (() => {
  const COLORS = ["#4ade80", "#60a5fa", "#f472b6", "#fbbf24", "#a78bfa", "#34d399",
                  "#f87171", "#38bdf8", "#fb923c", "#e879f9"];
  const PHASE_COLORS = { P0: "rgba(96,165,250,.10)", P1: "rgba(74,222,128,.12)",
                         P2: "rgba(251,191,36,.10)", P3: "rgba(248,113,113,.10)" };

  function create(canvas, opts = {}) {
    const ctx = canvas.getContext("2d");
    const state = {
      series: [],        // {label, unit, points:[[ms,v]], color, targets:[[ms,v]]|null}
      bands: [],         // {start, end, label} phase bands
      pins: [],          // {ts, kind, title}
      range: null,       // [ms, ms] view range
      dataRange: null,
      playhead: null,
      hover: null,
      dayAnchor: opts.dayAnchor || null,   // {startMs, flipMs|null} for day gridlines
      onSeek: opts.onSeek || null,
      padding: { l: 46, r: 46, t: 10, b: 22 },
    };

    function fullRange() {
      let lo = Infinity, hi = -Infinity;
      for (const s of state.series) for (const p of s.points) {
        if (p[0] < lo) lo = p[0];
        if (p[0] > hi) hi = p[0];
      }
      return lo < hi ? [lo, hi] : null;
    }

    function setData(series, extras = {}) {
      state.series = series.map((s, i) => ({ color: COLORS[i % COLORS.length], targets: null, ...s }));
      state.bands = extras.bands || [];
      state.pins = extras.pins || [];
      if (extras.dayAnchor !== undefined) state.dayAnchor = extras.dayAnchor;
      state.dataRange = fullRange();
      if (!state.range && state.dataRange) state.range = [...state.dataRange];
      draw();
    }

    function resetZoom() { state.range = state.dataRange ? [...state.dataRange] : null; draw(); }
    function setPlayhead(ms) { state.playhead = ms; draw(); }

    function plotArea() {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr; canvas.height = h * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const p = state.padding;
      return { x: p.l, y: p.t, w: w - p.l - p.r, h: h - p.t - p.b, W: w, H: h };
    }

    const xOf = (ms, a) => a.x + ((ms - state.range[0]) / (state.range[1] - state.range[0])) * a.w;
    const msOf = (x, a) => state.range[0] + ((x - a.x) / a.w) * (state.range[1] - state.range[0]);

    function unitScales() {
      // Group series by unit; first two units get real axes, rest normalize to first.
      const units = [];
      for (const s of state.series) if (!units.includes(s.unit || "")) units.push(s.unit || "");
      const scales = {};
      for (const u of units.slice(0, 2).concat(units.length > 2 ? units.slice(2) : [])) {
        let lo = Infinity, hi = -Infinity;
        for (const s of state.series) {
          if ((s.unit || "") !== u) continue;
          const src = [s.points, s.targets || []];
          for (const arr of src) for (const p of arr) {
            if (p[0] < state.range[0] || p[0] > state.range[1]) continue;
            if (p[1] < lo) lo = p[1];
            if (p[1] > hi) hi = p[1];
          }
        }
        if (lo > hi) { lo = 0; hi = 1; }
        if (lo === hi) { lo -= 1; hi += 1; }
        const pad = (hi - lo) * 0.08;
        scales[u] = { lo: lo - pad, hi: hi + pad, axis: units.indexOf(u) };
      }
      return { scales, units };
    }

    function draw() {
      const a = plotArea();
      ctx.clearRect(0, 0, a.W, a.H);
      if (!state.range || !state.series.length) {
        ctx.fillStyle = "#9aa0a8"; ctx.font = "13px system-ui";
        ctx.fillText("No data - pick entities and a range.", a.x + 8, a.y + 24);
        return;
      }
      const { scales, units } = unitScales();
      const yOf = (v, u) => {
        const sc = scales[u] || scales[units[0]] || { lo: 0, hi: 1 };
        return a.y + a.h - ((v - sc.lo) / (sc.hi - sc.lo)) * a.h;
      };

      // Phase bands
      for (const b of state.bands) {
        const x1 = Math.max(xOf(b.start, a), a.x), x2 = Math.min(xOf(b.end, a), a.x + a.w);
        if (x2 <= a.x || x1 >= a.x + a.w) continue;
        ctx.fillStyle = PHASE_COLORS[b.label] || "rgba(154,160,168,.08)";
        ctx.fillRect(x1, a.y, x2 - x1, a.h);
        if (x2 - x1 > 26) {
          ctx.fillStyle = "rgba(232,234,237,.45)"; ctx.font = "10px system-ui";
          ctx.fillText(b.label, x1 + 3, a.y + 11);
        }
      }

      // Day gridlines (anchored to grow start; flip marked)
      if (state.dayAnchor) {
        const dayMs = 86400e3, anchor = state.dayAnchor.startMs;
        const spanDays = (state.range[1] - state.range[0]) / dayMs;
        const step = spanDays > 45 ? 7 : 1;
        const first = Math.ceil((state.range[0] - anchor) / dayMs / step) * step;
        ctx.font = "10px system-ui"; ctx.textAlign = "center";
        for (let d = first; anchor + d * dayMs < state.range[1]; d += step) {
          const x = xOf(anchor + d * dayMs, a);
          ctx.strokeStyle = "rgba(42,46,56,.7)"; ctx.beginPath();
          ctx.moveTo(x, a.y); ctx.lineTo(x, a.y + a.h); ctx.stroke();
          ctx.fillStyle = "#9aa0a8"; ctx.fillText("d" + (d + 1), x, a.y + a.h + 14);
        }
        if (state.dayAnchor.flipMs) {
          const x = xOf(state.dayAnchor.flipMs, a);
          if (x >= a.x && x <= a.x + a.w) {
            ctx.strokeStyle = "#fbbf24"; ctx.setLineDash([4, 3]); ctx.beginPath();
            ctx.moveTo(x, a.y); ctx.lineTo(x, a.y + a.h); ctx.stroke(); ctx.setLineDash([]);
            ctx.fillStyle = "#fbbf24"; ctx.fillText("flip", x, a.y + 10);
          }
        }
        ctx.textAlign = "left";
      }

      // Axes labels (first two unit groups)
      ctx.font = "10px system-ui";
      for (const u of units.slice(0, 2)) {
        const sc = scales[u], right = sc.axis === 1;
        ctx.fillStyle = "#9aa0a8";
        for (let i = 0; i <= 4; i++) {
          const v = sc.lo + ((sc.hi - sc.lo) * i) / 4, y = yOf(v, u);
          ctx.textAlign = right ? "left" : "right";
          ctx.fillText(v.toFixed(Math.abs(sc.hi - sc.lo) < 8 ? 1 : 0),
                       right ? a.x + a.w + 4 : a.x - 4, y + 3);
        }
      }
      ctx.textAlign = "left";

      // Target step-lines then series
      for (const s of state.series) {
        if (s.targets && s.targets.length) {
          ctx.strokeStyle = s.color; ctx.globalAlpha = 0.55; ctx.setLineDash([6, 4]);
          ctx.lineWidth = 1.4; ctx.beginPath();
          let started = false, lastY = 0;
          for (const [ms, v] of s.targets) {
            const x = xOf(ms, a), y = yOf(v, s.unit || "");
            if (!started) { ctx.moveTo(x, y); started = true; }
            else { ctx.lineTo(x, lastY); ctx.lineTo(x, y); }
            lastY = y;
          }
          ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
        }
        ctx.strokeStyle = s.color; ctx.lineWidth = 1.6; ctx.beginPath();
        let pen = false;
        for (const [ms, v] of s.points) {
          if (ms < state.range[0] || ms > state.range[1]) { pen = false; continue; }
          const x = xOf(ms, a), y = yOf(v, s.unit || "");
          if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
        }
        ctx.stroke();
      }

      // Journal pins
      for (const p of state.pins) {
        if (p.ts < state.range[0] || p.ts > state.range[1]) continue;
        const x = xOf(p.ts, a);
        ctx.fillStyle = p.kind === "state_change" ? "#60a5fa" : "#f472b6";
        ctx.beginPath(); ctx.moveTo(x, a.y + a.h);
        ctx.lineTo(x - 4, a.y + a.h - 8); ctx.lineTo(x + 4, a.y + a.h - 8);
        ctx.closePath(); ctx.fill();
      }

      // Playhead
      if (state.playhead && state.playhead >= state.range[0] && state.playhead <= state.range[1]) {
        const x = xOf(state.playhead, a);
        ctx.strokeStyle = "#e8eaed"; ctx.lineWidth = 1.2; ctx.beginPath();
        ctx.moveTo(x, a.y); ctx.lineTo(x, a.y + a.h); ctx.stroke();
      }

      // Crosshair + readout
      if (state.hover && state.hover.x >= a.x && state.hover.x <= a.x + a.w) {
        const ms = msOf(state.hover.x, a);
        ctx.strokeStyle = "rgba(232,234,237,.35)"; ctx.beginPath();
        ctx.moveTo(state.hover.x, a.y); ctx.lineTo(state.hover.x, a.y + a.h); ctx.stroke();
        const lines = [new Date(ms).toLocaleString()];
        for (const s of state.series) {
          const v = valueAt(s.points, ms);
          if (v !== null) lines.push(`${s.label}: ${v}`);
        }
        ctx.font = "11px system-ui";
        const w = Math.max(...lines.map(t => ctx.measureText(t).width)) + 12;
        const bx = Math.min(state.hover.x + 10, a.x + a.w - w), by = a.y + 6;
        ctx.fillStyle = "rgba(17,19,24,.92)"; ctx.fillRect(bx, by, w, lines.length * 15 + 8);
        ctx.strokeStyle = "#2a2e38"; ctx.strokeRect(bx, by, w, lines.length * 15 + 8);
        lines.forEach((t, i) => {
          ctx.fillStyle = i === 0 ? "#9aa0a8" : (state.series[i - 1] || {}).color || "#e8eaed";
          ctx.fillText(t, bx + 6, by + 15 * (i + 1));
        });
      }

      // Zoom selection
      if (drag && dragNow !== null) {
        ctx.fillStyle = "rgba(74,222,128,.12)";
        ctx.fillRect(Math.min(drag.x, dragNow), a.y, Math.abs(dragNow - drag.x), a.h);
      }
    }

    function valueAt(points, ms) {
      if (!points.length) return null;
      let best = null, bd = Infinity;
      for (const p of points) {
        const d = Math.abs(p[0] - ms);
        if (d < bd) { bd = d; best = p[1]; }
      }
      return bd < 45 * 60e3 ? best : null;
    }

    let drag = null, dragNow = null;
    canvas.addEventListener("mousedown", e => { drag = { x: e.offsetX }; dragNow = e.offsetX; });
    canvas.addEventListener("mousemove", e => {
      state.hover = { x: e.offsetX, y: e.offsetY };
      if (drag) dragNow = e.offsetX;
      draw();
    });
    canvas.addEventListener("mouseleave", () => { state.hover = null; drag = null; dragNow = null; draw(); });
    canvas.addEventListener("mouseup", e => {
      const a = plotArea();
      if (drag && Math.abs(e.offsetX - drag.x) > 8) {
        const r = [msOf(Math.min(drag.x, e.offsetX), a), msOf(Math.max(drag.x, e.offsetX), a)];
        state.range = r;
      } else if (drag && state.onSeek) {
        state.onSeek(msOf(e.offsetX, a));
      }
      drag = null; dragNow = null; draw();
    });
    canvas.addEventListener("dblclick", resetZoom);
    window.addEventListener("resize", draw);

    return { setData, setPlayhead, resetZoom, draw,
             getRange: () => state.range && [...state.range] };
  }

  return { create, COLORS };
})();
