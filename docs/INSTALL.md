# Installing GrowScope

Two supported setups. Both need an InfluxDB you own - the add-on store one, or any container. GrowScope provisions its own database inside it and touches nothing else.

## HA OS / Supervised (the add-on)

1. Settings, Add-ons, Add-on store, three-dot menu, Repositories.
2. Add `https://github.com/JakeTheRabbit/growscope`, refresh the store.
3. Install GrowScope, start it, open it from the sidebar.
4. Data tab: InfluxDB URL and credentials, Save and test, Provision.
   - Store InfluxDB add-on URL is `http://a0d7b954-influxdb:8086` on most installs. v1 wants username and password. v2 wants org and token.
5. Grows tab: add a grow. Cameras tab: bind a camera entity, set the interval and the lights gate.

The add-on builds locally on install - first install takes a few minutes while Docker builds the image. Prebuilt images come later.

Add-on options:

| Option | Default | Notes |
|---|---|---|
| `log_level` | `info` | `debug` when something is being weird |
| `seconds_per_day` | `2.0` | Timelapse pacing. 2.0 makes a 70-day grow a 140 second video. Changing it only affects segments built after the change - rebuild to apply everywhere. |

### The companion integration (grow day sensors in HA)

1. HACS, Custom repositories, add this repo as type Integration, install, restart HA.
2. Settings, Devices and services, Add integration, GrowScope.
3. URL: a locally built add-on answers at `http://local-growscope:8099`. A store install has a repo-hash hostname instead - the add-on's Info page shows it. An IP and port also works.

You get a device per grow with `Day`, `Flower day`, and `Stage` sensors, polled each minute. Automations go from there - lights schedules keyed to flower day, notifications on stage change, whatever you like.

## HA Core / Container (plain Docker)

The engine runs standalone. It talks to HA's API directly instead of through the Supervisor, so it needs a URL and a long-lived access token (HA profile, Security, Long-lived access tokens).

Use [docker-compose.yml](docker-compose.yml) as the starting point:

```yaml
services:
  growscope:
    build: https://github.com/JakeTheRabbit/growscope.git#main:growscope
    container_name: growscope
    environment:
      - GROWSCOPE_HA_URL=http://192.168.1.10:8123
      - GROWSCOPE_HA_TOKEN=YOUR_LONG_LIVED_TOKEN
    volumes:
      - ./growscope-data:/data
      - ./growscope-media:/media/growscope
    ports:
      - "8099:8099"
    restart: unless-stopped
```

Then open `http://<host>:8099`. Same UI, same behavior.

Security, plainly: in standalone mode there is no ingress in front of the engine, so anyone who can reach port 8099 can use it. Keep it on your LAN or put your own auth proxy in front. Do not port-forward it to the internet.

## Where data lives

| What | Where | Backed up by |
|---|---|---|
| Registry (grows, cameras, settings) | Add-on private data / `./growscope-data` | HA backups / your own |
| Frames and timelapses | `/media/growscope/` | Whatever covers your media dir |
| Sensor history | Your InfluxDB | Your InfluxDB backup - it is your database |

## Upgrading

Add-on: update from the add-on store when a new version shows. Registry schema migrations run automatically on start. Standalone: pull and rebuild the container.
