# Changelog

## 0.1.0

First public cut. The engine core:

- Grow registry (SQLite, rides HA backups)
- Scheduled frame capture from any HA camera entity, lights-gated, via the Supervisor proxy
- Day-segment timelapse assembly - per-day encodes, concat to an always-current video, day-normalized pacing
- InfluxDB connect, autodetect v1/v2, provision, and 60s sensor recording for bound entities
- Ingress admin UI - grows, cameras, timelapses, data, status
- Runs standalone in plain Docker for HA Core users (set GROWSCOPE_HA_URL and GROWSCOPE_HA_TOKEN)

Not in yet, coming per the plan in docs/PLAN.md: native sidebar panel with HA entity pickers, replay and compare, photos and journal, recipes, crop steering overlays, grow bundles. No add-on icon yet either.
