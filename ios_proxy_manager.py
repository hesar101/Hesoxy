
#!/usr/bin/env python3

import getpass
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
import tempfile

try:
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:  # Non-Unix platforms.
    _HAS_TERMIOS = False

import paramiko
import psutil


# ==================================================
# Configuration
# ==================================================

LOCAL_SSH_PORT = 2222
DEVICE_SSH_PORT = 22

DEFAULT_PROXY_PORT = 8080

PREFERENCES_FILE = (
    "/private/var/preferences/SystemConfiguration/preferences.plist"
)

REMOTE_SCRIPT_PATH = "/var/mobile/ios_proxy_manager_remote.py"


# ==================================================
# Interactive arrow-key menu
# ==================================================

def _read_key():

    # Read a single keypress and normalize arrow keys,
    # Enter and Escape. Requires a Unix TTY.

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:

        tty.setraw(fd)

        ch = sys.stdin.read(1)

        if ch == "\x03":          # Ctrl-C
            raise KeyboardInterrupt

        if ch in ("\r", "\n"):    # Enter
            return "ENTER"

        if ch == "\x1b":          # Escape sequence (arrow keys)

            seq = sys.stdin.read(2)

            if seq == "[A":
                return "UP"

            if seq == "[B":
                return "DOWN"

            return "ESC"

        return ch

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _select_menu_numeric(title, labels):

    # Fallback used when there is no interactive TTY
    # (for example when input is piped).

    print(title)

    for index, label in enumerate(labels, 1):
        print(f"[{index}] {label}")

    while True:

        try:
            choice = int(input("\nSelect: "))

            if 1 <= choice <= len(labels):
                return choice - 1

        except (ValueError, EOFError):
            pass

        print("Invalid selection.")


def select_menu(title, labels):

    # Present labels and let the user move with the arrow
    # keys, confirming with Enter. Returns the chosen index.

    if not (_HAS_TERMIOS and sys.stdin.isatty()):
        return _select_menu_numeric(title, labels)

    index = 0

    print(title)
    print("(Use the up/down arrow keys, then press Enter)\n")

    def render(first):

        if not first:
            # Move the cursor back up over the rendered rows.
            sys.stdout.write(f"\x1b[{len(labels)}A")

        for i, label in enumerate(labels):

            pointer = ">" if i == index else " "
            line = f" {pointer} {label}"

            if i == index:
                line = f"\x1b[7m{line}\x1b[0m"   # Reverse video.

            sys.stdout.write("\x1b[2K" + line + "\n")

        sys.stdout.flush()

    render(first=True)

    while True:

        key = _read_key()

        if key == "UP":
            index = (index - 1) % len(labels)
            render(first=False)

        elif key == "DOWN":
            index = (index + 1) % len(labels)
            render(first=False)

        elif key == "ENTER":
            return index

        elif key == "ESC":
            raise KeyboardInterrupt


# ==================================================
# Host IP discovery
# ==================================================

def get_host_ips():

    addresses = []

    for interface, entries in psutil.net_if_addrs().items():

        for entry in entries:

            if entry.family != socket.AF_INET:
                continue

            ip = entry.address

            if ip.startswith("127."):
                continue

            if ip.startswith("169.254."):
                continue

            addresses.append({
                "interface": interface,
                "ip": ip
            })

    return addresses


def select_host_ip():

    addresses = get_host_ips()

    if not addresses:
        raise RuntimeError("No host IP addresses found.")

    labels = [
        f"{item['ip']} ({item['interface']})"
        for item in addresses
    ]

    index = select_menu(
        "\nSelect the host interface:",
        labels
    )

    return addresses[index]["ip"]


# ==================================================
# iproxy
# ==================================================

def start_iproxy():

    iproxy_path = shutil.which("iproxy")

    if not iproxy_path:
        raise RuntimeError(
            "iproxy not found. Install libimobiledevice."
        )

    print(
        f"\n[*] Starting USB forwarding: "
        f"localhost:{LOCAL_SSH_PORT} -> "
        f"iPhone:{DEVICE_SSH_PORT}"
    )

    process = subprocess.Popen(
        [
            iproxy_path,
            str(LOCAL_SSH_PORT),
            str(DEVICE_SSH_PORT)
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    for _ in range(30):

        if process.poll() is not None:
            raise RuntimeError("iproxy terminated unexpectedly.")

        try:

            with socket.create_connection(
                ("127.0.0.1", LOCAL_SSH_PORT),
                timeout=1
            ):
                print("[+] iproxy is ready.")
                return process

        except OSError:
            time.sleep(0.5)

    process.terminate()

    raise RuntimeError(
        "Could not establish USB SSH forwarding. "
        "Check that the iPhone is connected and trusted."
    )


# ==================================================
# SSH connection
# ==================================================

def connect_iphone():

    username = input(
        "\niOS username [mobile]: "
    ).strip() or "mobile"

    password = getpass.getpass(
        f"Password for {username}: "
    )

    client = paramiko.SSHClient()

    # Convenience for first connection.
    # For production, verify and pin the iPhone host key.
    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy()
    )

    print("\n[*] Connecting to iPhone...")

    client.connect(
        hostname="127.0.0.1",
        port=LOCAL_SSH_PORT,
        username=username,
        password=password,
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
        look_for_keys=False,
        allow_agent=False
    )

    print("[+] SSH connection established.")

    return client, username


# ==================================================
# Remote Python script
# ==================================================

REMOTE_SCRIPT = r'''
import os
import sys
import stat
import plistlib
import shutil
import tempfile
from datetime import datetime


PREF = (
    "/private/var/preferences/"
    "SystemConfiguration/preferences.plist"
)


def main():

    if len(sys.argv) < 2:
        raise SystemExit("Missing action.")

    action = sys.argv[1]

    proxy_ip = sys.argv[2] if len(sys.argv) > 2 else ""

    proxy_port = (
        int(sys.argv[3])
        if len(sys.argv) > 3 else 8080
    )

    if action not in ("set", "delete"):
        raise SystemExit("Invalid action.")

    if action == "set":

        if not proxy_ip:
            raise SystemExit("Proxy IP is required.")

        if not 1 <= proxy_port <= 65535:
            raise SystemExit("Invalid proxy port.")

    if not os.path.exists(PREF):
        raise SystemExit("Preferences plist not found.")

    print("[*] Reading preferences plist...")

    with open(PREF, "rb") as f:
        data = plistlib.load(f)

    services = data.get("NetworkServices", {})

    wifi_services = []

    for service_id, service in services.items():

        interface = service.get("Interface", {})

        device = interface.get("DeviceName", "")
        hardware = interface.get("Hardware", "")

        if (
            device == "en0"
            or hardware in ("AirPort", "Wi-Fi", "IEEE80211")
        ):
            wifi_services.append(
                (service_id, service)
            )

    if not wifi_services:
        raise SystemExit(
            "No Wi-Fi network service identified. "
            "No changes made."
        )

    print(
        "[+] Found Wi-Fi services:",
        len(wifi_services)
    )

    backup = (
        PREF + ".backup." +
        datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    shutil.copy2(PREF, backup)

    print("[+] Backup created:", backup)

    modified = 0

    for service_id, service in wifi_services:

        proxies = service.setdefault("Proxies", {})

        if action == "delete":

            keys = [
                "HTTPEnable",
                "HTTPProxy",
                "HTTPPort",
                "HTTPSEnable",
                "HTTPSProxy",
                "HTTPSPort",
                "SOCKSEnable",
                "SOCKSProxy",
                "SOCKSPort",
                "ProxyAutoConfigEnable",
                "ProxyAutoConfigURLString",
                "ProxyAutoDiscoveryEnable"
            ]

            for key in keys:
                proxies.pop(key, None)

            print(
                "[+] Proxy settings deleted for:",
                service_id
            )

        elif action == "set":

            proxies.update({

                "HTTPEnable": 1,
                "HTTPProxy": proxy_ip,
                "HTTPPort": proxy_port,

                "HTTPSEnable": 1,
                "HTTPSProxy": proxy_ip,
                "HTTPSPort": proxy_port

            })

            print(
                "[+] Proxy configured:",
                proxy_ip,
                proxy_port,
                service_id
            )

        modified += 1

    if not modified:
        raise SystemExit("Nothing modified.")

    # Write atomically while preserving file permissions.

    original_mode = stat.S_IMODE(os.stat(PREF).st_mode)

    directory = os.path.dirname(PREF)

    fd, temp_path = tempfile.mkstemp(
        prefix="preferences.",
        dir=directory
    )

    try:

        with os.fdopen(fd, "wb") as f:

            plistlib.dump(
                data,
                f,
                fmt=plistlib.FMT_BINARY
            )

        os.chmod(temp_path, original_mode)

        os.replace(temp_path, PREF)

    except Exception:

        if os.path.exists(temp_path):
            os.unlink(temp_path)

        raise

    print("[+] Preferences plist updated.")
    print("[!] iOS may overwrite or ignore direct plist edits.")
    print("[*] Verify using scutil --proxy.")


if __name__ == "__main__":
    main()
'''


# ==================================================
# Upload remote script
# ==================================================

def upload_remote_script(client):

    print("\n[*] Uploading remote Python script...")

    sftp = client.open_sftp()

    try:

        with sftp.file(REMOTE_SCRIPT_PATH, "w") as remote_file:
            remote_file.write(REMOTE_SCRIPT)

    finally:
        sftp.close()

    print("[+] Remote script uploaded.")


# ==================================================
# Execute remote script
# ==================================================

def execute_remote_script(
    client,
    username,
    action,
    proxy_ip="",
    proxy_port=DEFAULT_PROXY_PORT
):

    args = [
        "python3",
        REMOTE_SCRIPT_PATH,
        action,
        proxy_ip,
        str(proxy_port)
    ]

    command = " ".join(
        shlex.quote(arg) for arg in args
    )

    if username == "root":

        print("\n[*] Executing as root...")

        stdin, stdout, stderr = client.exec_command(
            command,
            get_pty=False
        )

    else:

        # Check sudo availability before prompting.
        stdin, stdout, stderr = client.exec_command(
            "command -v sudo"
        )

        sudo_path = stdout.read().decode().strip()

        if not sudo_path:

            raise RuntimeError(
                "sudo is not installed or unavailable. "
                "Reconnect directly as root."
            )

        password = getpass.getpass(
            "\nEnter MOBILE user's sudo password: "
        )

        print("\n[*] Executing with sudo...")

        command = (
            "sudo -S -p '' " + command
        )

        stdin, stdout, stderr = client.exec_command(
            command,
            get_pty=False
        )

        stdin.write(password + "\n")
        stdin.flush()
        stdin.channel.shutdown_write()

    output = stdout.read().decode(
        errors="replace"
    )

    errors = stderr.read().decode(
        errors="replace"
    )

    exit_code = stdout.channel.recv_exit_status()

    print("\n========== REMOTE OUTPUT ==========")

    if output:
        print(output)

    if errors:
        print("========== REMOTE ERRORS ==========")
        print(errors)

    print("===================================")

    print("Exit code:", exit_code)

    if exit_code != 0:
        raise RuntimeError(
            "Remote proxy operation failed."
        )

    print("[+] Remote operation completed.")


# ==================================================
# Proxy menu
# ==================================================

def proxy_menu():

    labels = [
        "Delete Proxy",
        "Set Proxy",
        "Exit"
    ]

    index = select_menu(
        "\n========== iOS Proxy Manager ==========",
        labels
    )

    # Return "1", "2" or "3" to match the main dispatcher.
    return str(index + 1)


# ==================================================
# Main
# ==================================================

def main():

    iproxy_process = None
    client = None

    try:

        print("\n=======================================")
        print("           iOS Proxy Manager")
        print("=======================================")

        # Step 1: Select host IP.

        host_ip = select_host_ip()

        print("\n[+] Selected host IP:", host_ip)

        # Step 2: Start USB forwarding.

        iproxy_process = start_iproxy()

        # Step 3: Connect over SSH.

        client, username = connect_iphone()

        # Step 4: Upload remote script.

        upload_remote_script(client)

        # Step 5: Select operation.

        choice = proxy_menu()

        if choice == "1":

            execute_remote_script(
                client,
                username,
                "delete"
            )

        elif choice == "2":

            port_input = input(
                f"\nProxy port [{DEFAULT_PROXY_PORT}]: "
            ).strip()

            port = int(
                port_input or DEFAULT_PROXY_PORT
            )

            if not 1 <= port <= 65535:
                raise ValueError("Invalid proxy port.")

            execute_remote_script(
                client,
                username,
                "set",
                host_ip,
                port
            )

        else:

            print("\n[*] Exiting.")

    except KeyboardInterrupt:

        print("\n[!] Interrupted.")

    except Exception as error:

        print(f"\n[-] Error: {error}")

    finally:

        if client:
            client.close()

        if iproxy_process:

            iproxy_process.terminate()

            try:
                iproxy_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                iproxy_process.kill()

        print("\n[*] Disconnected.")


if __name__ == "__main__":
    main()

