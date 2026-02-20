# tvmaster

A Raspberry Pi TV control hub that exposes HDMI-CEC commands as HTTP endpoints via Flask.

## Requirements

- Python 3.10+
- Flask (`pip install flask`)
- `cec` Python library — either:
  - `sudo apt install python3-cec` (system package), or
  - `pip install cec` (requires `sudo apt install libcec-dev build-essential python3-dev`)

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

Turn the TV on. Optionally pass a JSON body to switch to a specific HDMI input:

```json
{ "input": 1 }
```

`input` (1-4) corresponds to HDMI port number. If omitted, the TV turns on without changing input.

### `POST /tv/off`

Turn the TV off (standby).

## Running as a systemd service

Copy `tvmaster.service.example` to `/etc/systemd/system/tvmaster.service` and replace `YOUR_USER` with your username, then:

```
sudo systemctl daemon-reload
sudo systemctl enable --now tvmaster
```
