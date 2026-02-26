# tvmaster

A Raspberry Pi TV control server that manages a Samsung TV over a direct Ethernet connection using WebSocket (`samsungtvws`) and Wake-on-LAN.

## TV settings

Enable these in the TV's settings menu (General & Privacy > External Device Manager):

- **IP Remote** — allows WebSocket control (key commands for power off and HDMI switching)
- **Power on with Mobile** — allows Wake-on-LAN (keeps the network adapter active in standby)

## Network setup

The Pi connects directly to the TV via a USB Ethernet adapter (RTL8152B). The Pi runs a DHCP server on that interface so the TV gets an IP address. No router or internet access is needed for this link.

### 1. Assign a static IP to the USB Ethernet adapter

Create `/etc/systemd/network/10-tv-link.network` (replace `eth1` with your interface name):

```ini
[Match]
Name=eth1

[Network]
Address=10.0.0.1/24
```

Then restart networking:

```
sudo systemctl restart systemd-networkd
```

### 2. Install and configure dnsmasq

```
sudo apt install dnsmasq
sudo cp dnsmasq.conf.example /etc/dnsmasq.d/tvmaster.conf
```

Edit `/etc/dnsmasq.d/tvmaster.conf` and set the correct interface name, then:

```
sudo systemctl restart dnsmasq
```

## Setup

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the example config and fill in your TV's MAC address (found in the TV's settings under General & Privacy > Network > Network Status):

```
cp config.json.example config.json
```

```json
{
  "tv_ip": "10.0.0.100",
  "tv_mac": "AA:BB:CC:DD:EE:FF"
}
```

### Environment variables

| Variable    | Description                               | Default   |
|-------------|-------------------------------------------|-----------|
| `LOG_LEVEL` | Werkzeug log level (e.g. `DEBUG`, `INFO`) | `WARNING` |

### Command-line options

```
python3 server.py [--lan-port PORT] [--lan-host HOST]
```

| Flag         | Description     | Default  |
|--------------|-----------------|----------|
| `--lan-port` | LAN-facing port | `8080`   |
| `--lan-host` | LAN bind address| `0.0.0.0`|

## Endpoints

### `GET /tv/status`

Returns the TV power state by checking if the TV's HTTP API is reachable.

```json
{ "ok": true, "message": "on" }
```

### `POST /tv/on`

Wake the TV via Wake-on-LAN and switch to an HDMI input. Requires a JSON body:

```json
{ "input": 1 }
```

`input` (1-4) corresponds to HDMI port number.

### `POST /tv/off`

Turn the TV off (standby) by sending `KEY_POWER` via WebSocket.

## Running as a systemd service

Copy `tvmaster.service.example` to `/etc/systemd/system/tvmaster.service` and replace `YOUR_USER` with your username, then:

```
sudo systemctl daemon-reload
sudo systemctl enable --now tvmaster
```
