# GrowScope

Grow analytics engine for Home Assistant. Captures timelapse frames from any camera entity on a schedule, assembles always-current grow timelapses, keeps a grow registry, and records your bound sensors to InfluxDB at full resolution.

## Before you start

You need an InfluxDB to point it at. Two ways:

- Install the InfluxDB add-on from the add-on store. Two clicks, done.
- Or run InfluxDB as a separate Docker container anywhere on your network.

GrowScope does not ship a database. Your data lives in a database you own.

## Setup

1. Start the add-on. Open the GrowScope panel in the sidebar.
2. Data tab - enter your InfluxDB URL and credentials, hit Test, then Provision. GrowScope creates its own database and never touches anything else.
3. Grows tab - add a grow. Name, room, start date. Set the flip date when you flip.
4. Cameras tab - bind a camera entity to the grow. Set the capture interval and either a lights entity or a fixed lights-on window. Frames only get captured when the lights are on.
5. Timelapses tab - hit Build, or wait for the nightly build. The current timelapse always runs up to the most recent frame.

## Options

| Option | Default | What it does |
|---|---|---|
| `log_level` | `info` | Engine log verbosity |
| `seconds_per_day` | `2.0` | Timelapse pacing. Every grow day becomes this many seconds of video, so day N sits at the same timestamp in every grow's timelapse. |

## Where things live

- Frames: `/media/growscope/frames/<grow>/<camera>/<date>/`
- Timelapses: `/media/growscope/timelapses/`
- Registry: add-on private data, included in HA backups

Frames and timelapses are visible in the HA Media Browser.

## Failure modes worth knowing

- If a camera snapshot fails, that tick gets skipped and logged. Check Status tab, then check the camera works in HA itself.
- If InfluxDB is down, sensor recording pauses and resumes when it comes back. Frames keep capturing regardless - the two pipelines are independent.
- Timelapse build needs at least one full day of frames before it produces anything worth watching.
