# iOS Proxy Manager

A command-line tool, run from your Mac/Linux machine, that sets or removes the
HTTP/HTTPS proxy on a **jailbroken iOS device** over a USB connection.

It talks to the device over SSH (tunnelled through USB with `iproxy`), uploads a
small helper script, and edits the device's network `preferences.plist` directly
so you don't have to tap through the iOS Settings UI every time.

> ⚠️ **Jailbroken devices only.** This edits a protected system plist and needs
> root (or `sudo`) on the device. iOS may re-apply or ignore direct plist edits,
> so always verify the result afterwards.

---

## Features

- Discovers your machine's network interfaces and lets you pick which host IP the
  device should proxy through, using an **arrow-key menu** (no typing numbers).
- Tunnels SSH over USB with `iproxy`, so no Wi-Fi/network path to the device is
  required.
- **Set** an HTTP + HTTPS proxy pointing at your host, or **delete** all proxy
  settings, from a simple arrow-key menu.
- Backs up `preferences.plist` on the device before every change.
- Writes the plist atomically and preserves the original file permissions.

---

## Requirements

### On your computer (host)

- **Python 3** with the required packages:
  ```bash
  pip3 install -r requirements.txt
  ```
- **libimobiledevice**, which provides `iproxy` (used for USB SSH forwarding):
  ```bash
  brew install libimobiledevice        # macOS
  # or: sudo apt install libimobiledevice-utils   (Debian/Ubuntu)
  ```

### On the iOS device

- A **jailbroken** device with an SSH server (e.g. OpenSSH / Dropbear from your
  package manager).
- **Python 3** installed on the device (available in most jailbreak repos).
- Root access — either log in as `root`, or as `mobile` with `sudo` available.
- The device connected by USB and **trusted** ("Trust This Computer").

---

## Usage

```bash
python3 ios_proxy_manager.py
```

The tool walks you through these steps:

1. **Select the host interface** — use the ↑/↓ arrow keys to highlight the IP
   address the device should send traffic to, then press **Enter**.
2. **USB forwarding** — `iproxy` is started automatically, mapping
   `localhost:2222` on your machine to port `22` on the device.
3. **SSH login** — enter the iOS username (defaults to `mobile`) and its
   password. If you log in as `mobile`, you'll also be asked for the `sudo`
   password so the helper can run as root.
4. **Choose an operation** with the arrow keys:
   - **Delete Proxy** — removes all HTTP/HTTPS/SOCKS/PAC proxy keys.
   - **Set Proxy** — asks for a port (default `8080`) and configures the proxy
     to point at the host IP you selected in step 1.
   - **Exit**.

After a change, verify it on the device with:

```bash
scutil --proxy
```

### Keyboard controls

| Key        | Action                     |
| ---------- | -------------------------- |
| ↑ / ↓      | Move the selection         |
| Enter      | Confirm the highlighted row |
| Esc / Ctrl-C | Cancel                   |

If the tool is run without an interactive terminal (for example, with input
piped in), it automatically falls back to a numbered-list prompt.

---

## Configuration

A few constants at the top of `ios_proxy_manager.py` can be adjusted:

| Constant             | Default | Meaning                                              |
| -------------------- | ------- | ---------------------------------------------------- |
| `LOCAL_SSH_PORT`     | `2222`  | Local port `iproxy` listens on.                      |
| `DEVICE_SSH_PORT`    | `22`    | SSH port on the iOS device.                          |
| `DEFAULT_PROXY_PORT` | `8080`  | Proxy port offered by default when setting a proxy.  |
| `REMOTE_SCRIPT_PATH` | `/var/mobile/ios_proxy_manager_remote.py` | Where the helper script is uploaded on the device. |

---

## How it works

```
┌────────────┐   iproxy (USB)   ┌──────────────┐
│  Your Mac  │ ───────────────▶ │  iOS device  │
│   manager  │   SSH :2222→:22  │  (jailbroken)│
└────────────┘                  └──────────────┘
      │                                  │
      │ 1. pick host IP (arrow menu)     │
      │ 2. start iproxy USB tunnel       │
      │ 3. SSH in (paramiko)             │
      │ 4. upload remote helper ─────────┤
      │ 5. run helper as root ───────────┤
      │                                  ▼
      │                      reads/writes preferences.plist:
      │                      - backs it up (timestamped)
      │                      - finds the Wi-Fi network service
      │                      - sets or clears proxy keys
      │                      - writes atomically, keeps permissions
```

The remote helper (embedded in `ios_proxy_manager.py` as `REMOTE_SCRIPT`) only touches the
Wi-Fi network service — identified by interface `en0` or a Wi-Fi hardware type —
and makes **no changes** if no Wi-Fi service is found.

---

## Safety notes

- A timestamped backup of `preferences.plist` is created on the device before
  every change (e.g. `preferences.plist.backup.20260101_120000`).
- The SSH client uses `AutoAddPolicy`, which accepts the device host key on
  first connect. This is fine for a direct USB-local tunnel; for stronger
  security, pin the device's host key instead.
- iOS sometimes overwrites or ignores direct plist edits — always confirm with
  `scutil --proxy` and by checking **Settings → Wi-Fi → (network) → Configure
  Proxy**.

---

## Troubleshooting

| Symptom | Likely cause / fix |
| ------- | ------------------ |
| `iproxy not found` | Install `libimobiledevice`. |
| `Could not establish USB SSH forwarding` | Device not connected/trusted, or SSH not running on the device. |
| SSH authentication fails | Wrong username/password; try `root` directly. |
| `sudo is not installed or unavailable` | Log in as `root` instead of `mobile`. |
| `No Wi-Fi network service identified` | The device has no configured Wi-Fi service; no changes are made. |
| Proxy doesn't take effect | iOS may have ignored the edit — toggle Wi-Fi off/on, or set it manually once. |
