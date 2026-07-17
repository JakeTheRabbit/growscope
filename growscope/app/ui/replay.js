/* GrowScope replay: two grows side by side on the same day-of-cycle clock.

   The master video IS the clock - the day is derived from currentTime through
   the lapse manifest, nothing ever writes the master's clock during playback,
   so drift and backward jumps are structurally impossible. The follower is
   corrective-seeked only when it strays past a threshold.

   The timeline is a canvas-drawn shared scrubber: a week ruler in cycle-day
   space, one clip track per pane showing actual footage coverage, the flip
   marker, journal/photo pins on their grow's track, and a draggable playhead. */
"use strict";

const GSReplay = (() => {
  const $ = id => document.getElementById(id);
  const DAY = 86400e3;
  const api = p => fetch("api/" + p).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });

  const state = {
    lapses: [], grows: [],
    panes: [null, null],   // {video, manifest, grow, photos, journal, spd}
    align: "flip",
    master: 0,
    raf: 0,
  };

  const dayNum = (dateStr, anchorStr) =>
    Math.round((new Date(dateStr + "T00:00:00") - new Date(anchorStr + "T00:00:00")) / DAY) + 1;

  function axisPos(pane, dateStr) {
    const g = pane.grow;
    if (state.align === "flip" && g.flip_date) return dayNum(dateStr, g.flip_date);
    return dayNum(dateStr, g.start_date);
  }

  function dateForAxis(pane, pos) {
    const g = pane.grow;
    const anchor = (state.align === "flip" && g.flip_date) ? g.flip_date : g.start_date;
    const d = new Date(anchor + "T00:00:00");
    d.setDate(d.getDate() + (pos - 1));
    // Format in LOCAL time - toISOString() shifts to UTC and lands a day early
    // for anyone east of Greenwich, which desyncs the follower by one day.
    const pad = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function nearestIdx(days, dateStr) {
    let best = 0, bd = Infinity;
    for (let i = 0; i < days.length; i++) {
      const d = Math.abs(new Date(days[i]) - new Date(dateStr));
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  async function loadOptions() {
    [state.lapses, state.grows] = await Promise.all([api("timelapses"), api("grows")]);
    const withManifest = state.lapses.filter(l => l.manifest);
    const opts = withManifest.map(l => {
      const g = state.grows.find(x => x.id === l.manifest.grow_id);
      const label = `${g ? g.name : l.manifest.grow} - ${l.manifest.camera} (${l.manifest.days.length}d)`;
      return `<option value="${l.name}">${label}</option>`;
    }).join("");
    $("rp-a").innerHTML = "<option value=''>pane A</option>" + opts;
    $("rp-b").innerHTML = "<option value=''>pane B</option>" + opts;
    $("rp-hint").textContent = withManifest.length
      ? "" : "No timelapses with manifests yet - build one on the Timelapses tab first.";
  }

  async function setPane(i, lapseName) {
    const slot = i === 0 ? "a" : "b";
    const video = $("rp-video-" + slot);
    if (!lapseName) { state.panes[i] = null; video.removeAttribute("src"); update(); drawTimeline(); return; }
    const l = state.lapses.find(x => x.name === lapseName);
    const grow = state.grows.find(g => g.id === l.manifest.grow_id) ||
                 { name: l.manifest.grow, start_date: l.manifest.start_date, flip_date: l.manifest.flip_date };
    const [photos, journal] = await Promise.all([
      grow.id ? api("photos/" + grow.id) : [],
      grow.id ? api("journal/" + grow.id) : [],
    ]);
    video.src = "media/timelapses/" + l.name;
    video.muted = true;
    state.panes[i] = { video, manifest: l.manifest, grow, photos, journal,
                       spd: l.manifest.seconds_per_day || 2 };
    pickMaster();
    update();
    drawTimeline();
  }

  function pickMaster() {
    const [a, b] = state.panes;
    if (a && b) state.master = b.manifest.days.length > a.manifest.days.length ? 1 : 0;
    else state.master = a ? 0 : 1;
  }

  const masterPane = () => state.panes[state.master];
  const otherPane = () => state.panes[1 - state.master];

  function paneDate(pane) {
    const idx = Math.min(Math.floor(pane.video.currentTime / pane.spd),
                         pane.manifest.days.length - 1);
    return pane.manifest.days[Math.max(idx, 0)];
  }

  function badge(pane, el) {
    if (!pane) { el.textContent = ""; return; }
    const date = paneDate(pane);
    const day = dayNum(date, pane.grow.start_date);
    const flower = pane.grow.flip_date ? dayNum(date, pane.grow.flip_date) : null;
    el.textContent = `${pane.grow.name} - ${date} - day ${day}` +
                     (flower && flower > 0 ? ` - flower ${flower}` : "");
  }

  function update() {
    const m = masterPane();
    badge(state.panes[0], $("rp-badge-a"));
    badge(state.panes[1], $("rp-badge-b"));
    if (!m) return;
    const date = paneDate(m);
    const f = otherPane();
    if (f) {
      const targetDate = dateForAxis(f, axisPos(m, date));
      const fIdx = nearestIdx(f.manifest.days, targetDate);
      const within = m.video.currentTime % m.spd;
      const targetCt = Math.min(fIdx * f.spd + within * (f.spd / m.spd),
                                Math.max(f.video.duration - 0.05, 0) || fIdx * f.spd);
      if (Math.abs(f.video.currentTime - targetCt) > 0.6 && isFinite(targetCt)) {
        f.video.currentTime = targetCt;
      }
      if (m.video.paused !== f.video.paused) {
        if (m.video.paused) f.video.pause();
        else f.video.play().catch(() => {});
      }
    }
    drawTimeline();
    window.dispatchEvent(new CustomEvent("gs-replay-tick", {
      detail: { date, grow_id: m.grow.id } }));
  }

  function tickLoop() {
    update();
    const m = masterPane();
    if (m && !m.video.paused && !m.video.ended) state.raf = requestAnimationFrame(tickLoop);
  }

  function play() {
    const m = masterPane();
    if (!m) return;
    if (m.video.paused) {
      m.video.play().catch(() => {});
      const f = otherPane();
      if (f) f.video.play().catch(() => {});
      $("rp-play").textContent = "Pause";
      cancelAnimationFrame(state.raf);
      state.raf = requestAnimationFrame(tickLoop);
    } else {
      m.video.pause();
      const f = otherPane();
      if (f) f.video.pause();
      $("rp-play").textContent = "Play";
    }
  }

  function setSpeed(rate) {
    for (const p of state.panes) if (p) p.video.playbackRate = rate;
  }

  function scrubToIdx(idx) {
    const m = masterPane();
    if (!m) return;
    idx = Math.max(0, Math.min(idx, m.manifest.days.length - 1));
    m.video.currentTime = Math.min(idx * m.spd + 0.01, (m.video.duration || idx * m.spd) - 0.01);
    update();
  }

  function scrubToAxis(pos) {
    const m = masterPane();
    if (!m) return;
    scrubToIdx(nearestIdx(m.manifest.days, dateForAxis(m, pos)));
  }

  /* ---- Canvas timeline ---- */

  const TL = { lo: 0, hi: 1, hits: [], drag: false, moved: 0 };
  const TRACK_COLORS = [
    { fill: "rgba(74,222,128,.22)", edge: "#4ade80" },
    { fill: "rgba(96,165,250,.22)", edge: "#60a5fa" },
  ];

  function axisRange() {
    let lo = Infinity, hi = -Infinity;
    for (const pane of state.panes) {
      if (!pane) continue;
      for (const d of pane.manifest.days) {
        const p = axisPos(pane, d);
        if (p < lo) lo = p;
        if (p > hi) hi = p;
      }
    }
    if (lo > hi) { lo = 1; hi = 2; }
    return [lo, hi];
  }

  function drawTimeline() {
    const canvas = $("rp-timeline");
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (!W) return;
    if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
      canvas.width = W * dpr; canvas.height = H * dpr;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    TL.hits = [];

    const m = masterPane();
    if (!m) {
      ctx.fillStyle = "#9aa0a8"; ctx.font = "12px system-ui";
      ctx.fillText("Pick a pane to get a timeline.", 8, 20);
      return;
    }

    const PAD = 8;
    const [lo, hi] = axisRange();
    TL.lo = lo; TL.hi = hi;
    const span = Math.max(hi - lo, 1);
    const xOf = pos => PAD + ((pos - lo) / span) * (W - PAD * 2);
    const pxPerDay = (W - PAD * 2) / span;
    const flipMode = state.align === "flip";

    const RULER_Y = 22, TRACK_H = 20, TRACK_GAP = 6;
    const trackY = i => RULER_Y + 8 + i * (TRACK_H + TRACK_GAP);

    // Playhead position in axis space
    const headPos = axisPos(m, paneDate(m));

    // Progress tint behind everything, up to the playhead
    ctx.fillStyle = "rgba(232,234,237,.05)";
    ctx.fillRect(PAD, 0, xOf(headPos) - PAD, H);

    // Ruler: minor tick per day when roomy, labeled tick per week always
    ctx.font = "10px system-ui"; ctx.textAlign = "center";
    const firstWeek = Math.ceil((lo - 1) / 7) * 7 + 1;
    if (pxPerDay >= 3.5) {
      ctx.strokeStyle = "rgba(42,46,56,.9)";
      for (let p = Math.ceil(lo); p <= hi; p++) {
        const x = xOf(p);
        ctx.beginPath(); ctx.moveTo(x, RULER_Y - 4); ctx.lineTo(x, RULER_Y); ctx.stroke();
      }
    }
    for (let p = firstWeek; p <= hi; p += 7) {
      const x = xOf(p);
      ctx.strokeStyle = "#2a2e38";
      ctx.beginPath(); ctx.moveTo(x, RULER_Y - 9); ctx.lineTo(x, H); ctx.stroke();
      ctx.fillStyle = "#9aa0a8";
      ctx.fillText((flipMode ? "f" : "d") + p, x, 11);
    }

    // Flip marker at f1 in flip mode; per-track notches in start mode
    if (flipMode) {
      const x = xOf(1);
      ctx.strokeStyle = "#fbbf24"; ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, 2); ctx.lineTo(x, H); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = "#fbbf24"; ctx.fillText("flip", x, H - 2);
    }

    // Clip tracks: contiguous footage runs per pane, gaps stay visible
    state.panes.forEach((pane, i) => {
      if (!pane) return;
      const y = trackY(i), c = TRACK_COLORS[i];
      const positions = pane.manifest.days.map(d => axisPos(pane, d)).sort((a, b) => a - b);
      let runStart = positions[0], prev = positions[0];
      const runs = [];
      for (let k = 1; k <= positions.length; k++) {
        if (k === positions.length || positions[k] !== prev + 1) {
          runs.push([runStart, prev]);
          if (k < positions.length) runStart = positions[k];
        }
        prev = positions[k];
      }
      for (const [r0, r1] of runs) {
        const x0 = xOf(r0 - 0.5), x1 = xOf(r1 + 0.5);
        ctx.fillStyle = c.fill;
        ctx.strokeStyle = c.edge;
        ctx.beginPath();
        ctx.roundRect(x0, y, Math.max(x1 - x0, 2), TRACK_H, 4);
        ctx.fill(); ctx.stroke();
      }
      ctx.fillStyle = "#e8eaed"; ctx.font = "11px system-ui"; ctx.textAlign = "left";
      ctx.fillText(pane.grow.name, xOf(runs[0][0] - 0.5) + 6, y + 14);
      ctx.textAlign = "center";
      if (!flipMode && pane.grow.flip_date) {
        const fx = xOf(axisPos(pane, pane.grow.flip_date));
        ctx.strokeStyle = "#fbbf24";
        ctx.beginPath(); ctx.moveTo(fx, y - 2); ctx.lineTo(fx, y + TRACK_H + 2); ctx.stroke();
      }
      // Pins on this pane's track
      for (const ph of pane.photos) {
        const x = xOf(axisPos(pane, ph.ts.slice(0, 10)));
        ctx.fillStyle = "#f472b6";
        ctx.beginPath(); ctx.moveTo(x, y - 1); ctx.lineTo(x - 4, y - 7); ctx.lineTo(x + 4, y - 7);
        ctx.closePath(); ctx.fill();
        TL.hits.push({ x0: x - 5, x1: x + 5, y0: y - 8, y1: y,
                       action: () => openLightbox(axisPos(pane, ph.ts.slice(0, 10))) });
      }
      for (const j of pane.journal) {
        const pos = axisPos(pane, j.ts.slice(0, 10));
        const x = xOf(pos);
        ctx.fillStyle = "#60a5fa";
        ctx.fillRect(x - 1.5, y + TRACK_H - 6, 3, 6);
        TL.hits.push({ x0: x - 4, x1: x + 4, y0: y + TRACK_H - 8, y1: y + TRACK_H,
                       action: () => scrubToAxis(pos), title: j.title });
      }
    });

    // Playhead over everything
    const hx = xOf(headPos);
    ctx.strokeStyle = "#e8eaed"; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(hx, 2); ctx.lineTo(hx, H); ctx.stroke();
    ctx.fillStyle = "#e8eaed";
    ctx.beginPath(); ctx.moveTo(hx, 8); ctx.lineTo(hx - 5, 2); ctx.lineTo(hx + 5, 2);
    ctx.closePath(); ctx.fill();
    ctx.lineWidth = 1;
  }

  function timelinePos(ev) {
    const canvas = $("rp-timeline");
    const rect = canvas.getBoundingClientRect();
    const frac = Math.min(Math.max((ev.clientX - rect.left - 8) / (rect.width - 16), 0), 1);
    return TL.lo + frac * (TL.hi - TL.lo);
  }

  function nearestPhoto(pane, pos, tolDays = 3) {
    let best = null, bd = Infinity;
    for (const ph of pane.photos) {
      const d = Math.abs(axisPos(pane, ph.ts.slice(0, 10)) - pos);
      if (d < bd) { bd = d; best = ph; }
    }
    return bd <= tolDays ? best : null;
  }

  function openLightbox(pos) {
    const box = $("rp-lightbox");
    const cells = state.panes.map(pane => {
      if (!pane) return "<div class='rp-lb-cell'>no pane</div>";
      const ph = nearestPhoto(pane, pos);
      if (!ph) return `<div class='rp-lb-cell'>${pane.grow.name}: no photo within 3 days</div>`;
      const day = dayNum(ph.ts.slice(0, 10), pane.grow.start_date);
      return `<div class='rp-lb-cell'><img src="media/${ph.path}">` +
             `<div>${pane.grow.name} - ${ph.ts.slice(0, 10)} - day ${day}</div></div>`;
    });
    box.querySelector(".rp-lb-row").innerHTML = cells.join("");
    box.style.display = "flex";
    scrubToAxis(pos);
  }

  function init() {
    $("rp-a").onchange = e => setPane(0, e.target.value);
    $("rp-b").onchange = e => setPane(1, e.target.value);
    $("rp-align").onchange = e => { state.align = e.target.value; update(); drawTimeline(); };
    $("rp-speed").onchange = e => setSpeed(parseFloat(e.target.value));
    $("rp-play").onclick = play;
    $("rp-lightbox").onclick = () => { $("rp-lightbox").style.display = "none"; };

    const tl = $("rp-timeline");
    tl.addEventListener("pointerdown", ev => {
      TL.drag = true; TL.moved = 0;
      tl.setPointerCapture(ev.pointerId);
      scrubToAxis(timelinePos(ev));
    });
    tl.addEventListener("pointermove", ev => {
      if (!TL.drag) return;
      TL.moved++;
      scrubToAxis(timelinePos(ev));
    });
    tl.addEventListener("pointerup", ev => {
      TL.drag = false;
      if (TL.moved < 3) {
        const rect = tl.getBoundingClientRect();
        const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
        const hit = TL.hits.find(h => x >= h.x0 && x <= h.x1 && y >= h.y0 && y <= h.y1);
        if (hit) hit.action();
      }
    });
    window.addEventListener("resize", drawTimeline);

    for (const slot of ["a", "b"]) {
      const v = $("rp-video-" + slot);
      v.addEventListener("timeupdate", update);   // keeps badges honest in background tabs
      v.addEventListener("ended", () => { $("rp-play").textContent = "Play"; });
    }
    loadOptions();
  }

  return { init, reload: loadOptions, scrubToAxis };
})();
