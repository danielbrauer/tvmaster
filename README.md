# netmaster

A Raspberry Pi TV control hub that exposes HDMI-CEC commands as HTTP endpoints via Flask.

## Requirements

- Python 3.6+
- Flask (`pip install flask`)
- `cec-client` (e.g. `sudo apt install cec-utils`)

## Configuration

### Environment variables

| Variable    | Description                          | Default   |
|-------------|--------------------------------------|-----------|
| `LOG_LEVEL` | Werkzeug log level (e.g. `DEBUG`, `INFO`) | `WARNING` |

### Command-line options

```
python3 server.py [--lan-port PORT] [--lan-host HOST]
```

| Flag         | Description                        | Default              |
|--------------|------------------------------------|----------------------|
| `--lan-port` | LAN-facing port                    | `8080`               |
| `--lan-host` | LAN bind address                   | `0.0.0.0`            |

## Endpoints

### `GET /tv/status`

Returns the TV power state.

```json
{ "ok": true, "message": "on" }
```

### `POST /tv/on`

Turn the TV on.

### `POST /tv/off`

Turn the TV off (standby).

## Running as a systemd service

Copy `netmaster.service.example` to `/etc/systemd/system/netmaster.service` and replace `YOUR_USER` with your username, then:

```
sudo systemctl daemon-reload
sudo systemctl enable --now netmaster
```
