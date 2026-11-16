#!/usr/bin/env python3
"""Deploy a .ptf firmware image to a Tracker+ or Tracker Mini over USB.

Uses the development console described in docs/developer_guide.md
(section 6). The running firmware must be built with ENABLE_DEV_CONSOLE and
the SD card must contain an empty /.develop file.

Steps:
  1. ping            find the device, note product and running build id
  2. storage on      switch to USB storage mode, close the port
  3. copy            write the .ptf to /Firmware/ on the card, flush, eject
  4. storage off     reopen the port, leave storage mode
     flash <name>    hand off to the bootloader, which flashes the file
  5. ping            wait for the device to come back, check the build id

Requires pyserial (python -m pip install pyserial). Under WSL2, run this
script with the Windows Python (python.exe) so it can see the COM port and
the drive letter; `make deploy-plus` does that for you.
"""

import argparse
import os
import platform
import string
import subprocess
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("deploy: pyserial is required: python -m pip install pyserial")

USB_VID = 0x16D0
USB_PIDS = {0x1118: "mini", 0x1119: "plus"}
NAME_PREFIX = {"plus": "TrackerPlus", "mini": "TrackerMini"}
# Same limits as the firmware updater's file filter and the dev console.
MAX_NAME_LENGTH = 31
MIN_NAME_LENGTH = 15
DEVELOP_MARKER = ".develop"
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"

verbose = False


class DeployError(Exception):
    pass


class DeviceFault(DeployError):
    pass


class PortGone(DeployError):
    pass


def info(message):
    print(f"deploy: {message}", flush=True)


# ---------------------------------------------------------------------------
# Serial console
# ---------------------------------------------------------------------------


def matching_ports():
    return [p for p in list_ports.comports() if p.vid == USB_VID and p.pid in USB_PIDS]


def find_port(explicit):
    if explicit:
        devices = [p.device for p in list_ports.comports()]
        return explicit if explicit in devices else None

    ports = matching_ports()
    if len(ports) > 1:
        names = ", ".join(p.device for p in ports)
        raise DeployError(f"several Trackers found ({names}); pick one with --port")
    return ports[0].device if ports else None


def wait_for_port(explicit, timeout, what):
    deadline = time.monotonic() + timeout
    while True:
        device = find_port(explicit)
        if device:
            return device
        if time.monotonic() >= deadline:
            raise DeployError(f"timed out after {timeout:.0f} s waiting for {what}")
        time.sleep(0.25)


def wait_for_port_gone(device, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if device not in [p.device for p in list_ports.comports()]:
            return True
        time.sleep(0.25)
    return False


class Console:
    """One open session on the dev console port."""

    def __init__(self, device, open_timeout=10.0):
        deadline = time.monotonic() + open_timeout
        while True:
            try:
                # Opening asserts DTR, which the device reads as "port open".
                self.port = serial.Serial(device, 115200, timeout=0.1)
                break
            except (serial.SerialException, OSError) as error:
                # Right after enumeration the OS may not let us in yet.
                if time.monotonic() >= deadline:
                    raise DeployError(f"cannot open {device}: {error}") from error
                time.sleep(0.25)
        self.device = device
        self.pending = b""

    def close(self):
        try:
            self.port.close()
        except (serial.SerialException, OSError):
            pass

    def _read_line(self, deadline):
        while b"\n" not in self.pending:
            if time.monotonic() >= deadline:
                return None
            try:
                chunk = self.port.read(256)
            except (serial.SerialException, OSError) as error:
                raise PortGone(str(error)) from error
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        return line.decode("utf-8", "replace").rstrip("\r")

    def read_reply(self, timeout):
        """Returns the next '@' reply line, or None on timeout. Log lines in
        between are printed with --verbose."""
        deadline = time.monotonic() + timeout
        while True:
            line = self._read_line(deadline)
            if line is None:
                return None
            if line.startswith("@fault"):
                report = [line]
                more = self._read_line(time.monotonic() + 1.0)
                if more and more.startswith("@fault"):
                    report.append(more)
                raise DeviceFault("device has faulted:\n  " + "\n  ".join(report))
            if line.startswith("@log dropped"):
                if verbose:
                    print(f"  | {line}")
                continue
            if line.startswith("@"):
                return line
            if verbose:
                print(f"  | {line}")

    def command(self, text, timeout=5.0):
        if verbose:
            print(f"  > {text}")
        try:
            self.port.write((text + "\n").encode())
            self.port.flush()
        except (serial.SerialException, OSError) as error:
            raise PortGone(str(error)) from error
        reply = self.read_reply(timeout)
        if reply is None:
            raise DeployError(f"no reply to '{text}' within {timeout:.0f} s")
        if verbose:
            print(f"  < {reply}")
        return reply


def ping(console):
    reply = console.command("ping")
    parts = reply.split()
    if len(parts) < 3 or parts[0] != "@tracker-dev":
        raise DeployError(f"unexpected reply to ping: {reply}")
    return parts[1], " ".join(parts[2:])


# ---------------------------------------------------------------------------
# SD card volume
# ---------------------------------------------------------------------------


def candidate_roots():
    if IS_WINDOWS:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # Keep Windows from popping "insert a disk" dialogs for empty readers.
        kernel32.SetErrorMode(0x0001 | 0x8000)
        mask = kernel32.GetLogicalDrives()
        return [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]

    roots = []
    if IS_MACOS:
        bases = ["/Volumes"]
    else:
        user = os.environ.get("USER", "")
        bases = [f"/media/{user}", f"/run/media/{user}", "/media", "/mnt"]
    for base in bases:
        try:
            roots += [os.path.join(base, name) for name in os.listdir(base)]
        except OSError:
            pass
    return roots


def card_volumes():
    """Mounted volumes that look like a Tracker card in develop mode."""
    found = []
    for root in candidate_roots():
        try:
            if os.path.isfile(os.path.join(root, DEVELOP_MARKER)):
                found.append(root)
        except OSError:
            pass
    return found


def normalize_drive(drive):
    if IS_WINDOWS and len(drive) == 2 and drive[1] == ":":
        return drive + "\\"
    return drive


def wait_for_volume(explicit, before, timeout):
    if explicit:
        deadline = time.monotonic() + timeout
        while not os.path.isdir(explicit):
            if time.monotonic() >= deadline:
                raise DeployError(f"timed out waiting for {explicit} to mount")
            time.sleep(0.5)
        return explicit

    deadline = time.monotonic() + timeout
    # If the card was already mounted (the device was left in storage mode),
    # no new volume will appear; use the known one after a short grace period.
    fallback_after = time.monotonic() + 5.0
    while True:
        now_mounted = card_volumes()
        new = [v for v in now_mounted if v not in before]
        if len(new) == 1:
            return new[0]
        if len(new) > 1:
            raise DeployError(f"several develop-mode cards mounted ({', '.join(new)}); pick one with --drive")
        if len(now_mounted) == 1 and time.monotonic() >= fallback_after:
            return now_mounted[0]
        if time.monotonic() >= deadline:
            hint = "" if now_mounted else " (no mounted volume has a /.develop file)"
            raise DeployError(f"timed out after {timeout:.0f} s waiting for the SD card to mount{hint}; "
                              "use --drive if your system does not auto-mount it")
        time.sleep(0.5)


def copy_firmware(source, volume, name):
    firmware_dir = os.path.join(volume, "Firmware")
    os.makedirs(firmware_dir, exist_ok=True)
    destination = os.path.join(firmware_dir, name)

    with open(source, "rb") as f:
        data = f.read()
    with open(destination, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if hasattr(os, "sync"):
        os.sync()

    written = os.path.getsize(destination)
    if written != len(data):
        raise DeployError(f"wrote {written} bytes to {destination}, expected {len(data)}")
    return destination


def eject(volume):
    """Best effort: the data is already flushed, ejecting just lets the OS
    let go of the volume cleanly before the device remounts the card."""
    try:
        if IS_WINDOWS:
            drive = volume.rstrip("\\")
            script = (f"(New-Object -ComObject Shell.Application).Namespace(17)"
                      f".ParseName('{drive}').InvokeVerb('Eject')")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                           check=True, capture_output=True, timeout=30)
        elif IS_MACOS:
            subprocess.run(["diskutil", "eject", volume], check=True, capture_output=True, timeout=30)
        else:
            source = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "--target", volume],
                                    check=True, capture_output=True, text=True, timeout=10).stdout.strip()
            subprocess.run(["udisksctl", "unmount", "-b", source], check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        info(f"warning: could not eject {volume} ({error}); continuing, the file is already flushed")
        return

    deadline = time.monotonic() + 15
    while os.path.exists(os.path.join(volume, DEVELOP_MARKER)) and time.monotonic() < deadline:
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def firmware_name(source, product, override):
    other = [p for k, p in NAME_PREFIX.items() if k != product]
    base = os.path.basename(source)
    if any(base.startswith(p) for p in other) and not override:
        raise DeployError(f"{base} looks like firmware for another device, but a Tracker {product} is connected")

    name = override or base
    prefix = NAME_PREFIX[product]
    valid = (name.startswith(prefix) and name.endswith(".ptf")
             and MIN_NAME_LENGTH <= len(name) <= MAX_NAME_LENGTH
             and not any(c in name for c in "/\\: "))
    if valid:
        return name
    if override:
        raise DeployError(f"--name {override}: must start with {prefix}, end with .ptf, "
                          f"be {MIN_NAME_LENGTH}-{MAX_NAME_LENGTH} characters and contain no spaces or path separators")
    fallback = f"{prefix}_deploy.ptf"
    info(f"'{base}' is not a valid name on the card (max {MAX_NAME_LENGTH} chars, prefix {prefix}); using {fallback}")
    return fallback


def deploy(args):
    source = os.path.abspath(args.ptf)
    if not os.path.isfile(source):
        raise DeployError(f"no such file: {source}")

    # 1. Find the device.
    try:
        device = wait_for_port(args.port, 5, "the Tracker dev console")
    except DeployError:
        raise DeployError(
            "no Tracker dev console found. Check that the running firmware was built with "
            "-DENABLE_DEV_CONSOLE, that the SD card has an empty /.develop file, and (under WSL2) "
            "that this script runs with the Windows Python") from None

    console = Console(device)
    try:
        product, old_build = ping(console)
        info(f"found Tracker {product} on {device}, running build {old_build}")
        name = firmware_name(source, product, args.name)

        # 2. Storage mode. The device re-enumerates only once the port closes.
        before = card_volumes()
        reply = console.command("storage on", timeout=10)
        if reply != "@ok storage on":
            raise DeployError(f"could not enter USB storage mode: {reply}")
    finally:
        console.close()

    # 3. Copy and eject.
    if args.drive:
        info(f"waiting for {args.drive} to mount...")
    else:
        info("waiting for the SD card to mount...")
    volume = wait_for_volume(normalize_drive(args.drive) if args.drive else None, before, args.mount_timeout)
    destination = copy_firmware(source, volume, name)
    info(f"copied {os.path.basename(source)} -> {destination}")
    eject(volume)

    # 4. Leave storage mode and flash. Ejecting can make the device leave
    # storage mode and re-enumerate by itself, so the port may vanish under
    # us once; reconnect and try again.
    deadline = time.monotonic() + 30
    while True:
        device = wait_for_port(args.port, max(1.0, deadline - time.monotonic()),
                               "the dev console after leaving storage mode")
        console = Console(device)
        try:
            reply = console.command("storage off", timeout=10)
            break
        except PortGone:
            console.close()
            if time.monotonic() >= deadline:
                raise DeployError("the dev console kept disconnecting after storage mode") from None
            time.sleep(1.0)
    try:
        if reply != "@ok storage off":
            raise DeployError(f"could not leave USB storage mode: {reply}")

        reply = console.command(f"flash {name}", timeout=10)
        if reply != "@ok flash pending":
            raise DeployError(f"flash refused: {reply}")

        # runFlash() runs ~250 ms later; any problem comes back as @err,
        # success shows up as the port going away when the MCU resets.
        try:
            late = console.read_reply(timeout=5)
        except PortGone:
            late = None
        if late and late.startswith("@err"):
            raise DeployError(f"flash failed: {late}")
    finally:
        console.close()

    info("device is resetting into the bootloader; waiting for it to come back...")
    wait_for_port_gone(device, 10)

    # 5. Check what is running now.
    try:
        device = wait_for_port(args.port, args.boot_timeout, "the device to restart")
    except DeployError as error:
        raise DeployError(f"{error}. If the bootloader finished, a Tracker Mini may need its power button; "
                          "if not, recover with the known-good .ptf in /Firmware/") from None

    console = Console(device)
    try:
        product, new_build = ping(console)
        if args.expect_build:
            if not new_build.startswith(args.expect_build):
                raise DeployError(f"device is running build {new_build}, expected {args.expect_build}")
            if old_build == new_build:
                info(f"note: the build id did not change ({new_build}), so this check cannot tell "
                     "the new image from the old one (uncommitted changes keep the same id)")
        elif old_build == new_build:
            info(f"warning: build id unchanged ({new_build}); was the flash skipped?")
        info(f"done: Tracker {product} on {device} is running build {new_build}")

        if args.follow:
            info("following the device log (Ctrl-C to stop)")
            while True:
                try:
                    chunk = console.port.read(256)
                except (serial.SerialException, OSError):
                    info("port closed")
                    break
                if chunk:
                    sys.stdout.write(chunk.decode("utf-8", "replace"))
                    sys.stdout.flush()
    finally:
        console.close()


def main():
    global verbose

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ptf", help="firmware image to deploy")
    parser.add_argument("--port", help="serial port of the dev console (default: find by USB id)")
    parser.add_argument("--drive", help="mount point / drive letter of the SD card in storage mode "
                                        "(default: the volume with a /.develop file that appears)")
    parser.add_argument("--name", help="file name to use in /Firmware/ (default: the source file name, "
                                       "or <Product>_deploy.ptf if that is not a valid name)")
    parser.add_argument("--expect-build", help="build id the device must report after flashing")
    parser.add_argument("--mount-timeout", type=float, default=60, help="seconds to wait for the card (default 60)")
    parser.add_argument("--boot-timeout", type=float, default=240,
                        help="seconds to wait for the device after flashing (default 240)")
    parser.add_argument("--follow", action="store_true", help="keep printing the device log after deploying")
    parser.add_argument("-v", "--verbose", action="store_true", help="show console traffic and device log")
    args = parser.parse_args()
    verbose = args.verbose

    try:
        deploy(args)
    except DeployError as error:
        sys.exit(f"deploy: error: {error}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
