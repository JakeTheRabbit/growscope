/* GrowScope replay: two grows side by side on the same day-of-cycle clock.

   The master video IS the clock - the day is derived from currentTime through
   the lapse manifest, nothing ever writes the master's clock during playback,
   so drift and backward jumps are structurally impossible. The follower is
   corrective-seeked only when it strays past a threshold. */
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
    if (!lapseName) { state.panes[i] = null; video.removeAttribute("src"); update(); return; }
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
    drawRail();
    update();
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
    // Scrub reflects the master's day index
    const idx = Math.min(Math.floor(m.video.currentTime / m.spd), m.manifest.days.length - 1);
    if (document.activeElement !== $("rp-scrub")) {
      $("rp-scrub").max = m.manifest.days.length - 1;
      $("rp-scrub").value = idx;
    }
    // Follower corrective seek
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

  function scrubTo(idx) {
    const m = masterPane();
    if (!m) return;
    m.video.currentTime = Math.min(idx * m.spd + 0.01, (m.video.duration || idx * m.spd) - 0.01);
    update();
  }

  function drawRail() {
    const rail = $("rp-rail");
    rail.innerHTML = "";
    const m = masterPane();
    if (!m) return;
    const positions = m.manifest.days.map(d => axisPos(m, d));
    const lo = Math.min(...positions), hi = Math.max(...positions);
    const place = pos => ((pos - lo) / Math.max(hi - lo, 1)) * 100;
    for (const pane of state.panes) {
      if (!pane) continue;
      for (const ph of pane.photos) {
        const pos = axisPos(pane, ph.ts.slice(0, 10));
        if (pos < lo || pos > hi) continue;
        const pin = document.createElement("div");
        pin.className = "rp-pin photo";
        pin.style.left = place(pos) + "%";
        pin.title = `${pane.grow.name} photo ${ph.ts.slice(0, 10)}`;
        pin.onclick = () => openLightbox(pos);
        rail.appendChild(pin);
      }
      for (const j of pane.journal) {
        const pos = axisPos(pane, j.ts.slice(0, 10));
        if (pos < lo || pos > hi) continue;
        const pin = document.createElement("div");
        pin.className = "rp-pin note";
        pin.style.left = place(pos) + "%";
        pin.title = `${pane.grow.name}: ${j.title}`;
        pin.onclick = () => {
          scrubTo(nearestIdx(m.manifest.days, dateForAxis(m, pos)));
        };
        rail.appendChild(pin);
      }
    }
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
    const m = masterPane();
    if (m) scrubTo(nearestIdx(m.manifest.days, dateForAxis(m, pos)));
  }

  function init() {
    $("rp-a").onchange = e => setPane(0, e.target.value);
    $("rp-b").onchange = e => setPane(1, e.target.value);
    $("rp-align").onchange = e => { state.align = e.target.value; drawRail(); update(); };
    $("rp-speed").onchange = e => setSpeed(parseFloat(e.target.value));
    $("rp-play").onclick = play;
    $("rp-scrub").oninput = e => scrubTo(parseInt(e.target.value, 10));
    $("rp-lightbox").onclick = () => { $("rp-lightbox").style.display = "none"; };
    for (const slot of ["a", "b"]) {
      const v = $("rp-video-" + slot);
      v.addEventListener("timeupdate", update);   // keeps badges honest in background tabs
      v.addEventListener("ended", () => { $("rp-play").textContent = "Play"; });
    }
    loadOptions();
  }

  return { init, reload: loadOptions };
})();
