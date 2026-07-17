# Changelog

## 0.2.1

- Replay gets a proper timeline scrubber: canvas-drawn shared ruler in
  cycle-day space (week labels, day ticks), one clip track per pane showing
  actual footage coverage with gaps visible, the flip marker, photo and
  journal pins on their grow's track, and a draggable playhead with progress
  tint. Replaces the bare range slider and the separate pin rail.

## 0.2.0

The analytics layer. Everything from the 0.1.0 "not in yet" list that ships as code:

- Charts tab: canvas chart engine - any recorded entities on one chart, per-unit axes, crosshair readout, drag-zoom, day gridlines anchored to the grow with the flip marked
- Recipes: weekly setpoint curves anchored to flip, editor grid, assign to grows, targets drawn as dashed step-lines on charts
- Replay tab: two grows side by side on the same day-of-cycle clock, aligned by flip or by start. The master video is the clock - day comes from the lapse manifest, so drift and backward jumps are structurally impossible
- Timelapse manifests: every build writes a day map next to the mp4 - replay handles missing days exactly
- Journal: manual entries, kinds, and auto-journal watches - a watched entity's state change (crop steering phase moves) lands on the timeline by itself
- Photos: multi-file upload with pure-python EXIF capture-time extraction, thumbnails on the Journal tab, pins on the replay rail, click a pin to compare both grows' nearest photos side by side
- Phase bands on charts from recorded string states, journal and photo pins on charts
- History API over InfluxDB v1 (InfluxQL) and v2 (Flux), autodetected
- Backfill: pull whatever raw history HA's recorder still holds into Influx so charts start populated
- Capture sources beyond camera entities: direct URL (Frigate latest.jpg, ESP cams) and watch folders (SMB inboxes)
- Grow bundles: export a grow as one zip (registry, journal, recipe, photos, series, timelapses), import someone else's and replay against it
- Immich: album-per-grow sync with real capture times
- Integration services: growscope.flip, chop, log_event, capture_now, build_timelapse - and a growscope_stage_changed event on the HA bus

Still not in: native sidebar panel with HA's own entity pickers (ingress UI covers it meanwhile), Lovelace cards, MQTT push capture, alert rules (use the day/stage sensors in HA automations).

## 0.1.0

## 0.1.0

First public cut. The engine core:

- Grow registry (SQLite, rides HA backups)
- Scheduled frame capture from any HA camera entity, lights-gated, via the Supervisor proxy
- Day-segment timelapse assembly - per-day encodes, concat to an always-current video, day-normalized pacing
- InfluxDB connect, autodetect v1/v2, provision, and 60s sensor recording for bound entities
- Ingress admin UI - grows, cameras, timelapses, data, status
- Runs standalone in plain Docker for HA Core users (set GROWSCOPE_HA_URL and GROWSCOPE_HA_TOKEN)

Not in yet, coming per the plan in docs/PLAN.md: native sidebar panel with HA entity pickers, replay and compare, photos and journal, recipes, crop steering overlays, grow bundles. No add-on icon yet either.
