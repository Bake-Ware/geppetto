# Geppetto

*HID sharing over two Pi Picos.*

**A wired, two-Pico USB HID bridge.** Drive a second machine's keyboard and
mouse from your main PC over a plain wire — no KVM box, no network, no software
on the target. Geppetto pulls the strings; the other machine is the puppet.

Two RP2040 Picos are soldered together and joined by a UART. One plugs into your
PC, the other into the target. A small host script captures your keyboard and
trackball and streams the HID reports across. Hit a hotkey to hand control over,
hit it again to take it back.

```
        USB                 UART0 (GP0<->GP1)               USB
  PC ========> [ Pico A ] ------ soldered ------ [ Pico B ] ========> Target
 (script)       bridge                            gadget        (real USB kbd+mouse)
```

## Why

- **No radio.** Unlike a WiFi/BT dongle there's nothing to sleep, drop, or pair.
  Sub-millisecond, deterministic; the wire is never the latency floor (USB polls
  at 1 ms).
- **Target needs nothing.** It just sees a USB keyboard and mouse. Works through
  VPNs, lock screens, BIOS, any OS — it's hardware HID.
- **One firmware, no roles to configure.** Both Picos run the same binary; which
  one is the bridge and which is the gadget is decided at runtime by where each
  is plugged in (see [How it works](#how-it-works)).
- **Carries its own driver.** The bridge shows up as a tiny read-only USB stick
  with the host client on it — plug it into any PC and the script is right there.

Great for a desk where one PC captures a laptop's screen (HDMI capture, software
KVM, etc.) and you just want to drive it without a second keyboard and mouse —
including high-button trackballs (the mouse descriptor is an 8-button one, sized
for things like the Elecom HUGE) that cheap hardware KVMs choke on.

## Hardware

Two Raspberry Pi Picos (or any RP2040 board), common ground, UART0 crossover:

| Pico A | Pico B | why |
|--------|--------|-----|
| GP0 (UART0 TX) | GP1 (UART0 RX) | A talks, B listens |
| GP1 (UART0 RX) | GP0 (UART0 TX) | B talks, A listens |
| GND ×n | GND ×n | common reference |

Link runs at 921600 baud. The crossover is symmetric, so it doesn't matter which
board ends up on the target.

## How it works

Both Picos run the identical firmware. Role is implicit in the data — there is no
master/slave flag:

- A board fed bytes on its **USB-CDC** serial port copies them verbatim out the
  UART → it's the *bridge*.
- A board fed bytes on its **UART** decodes them into USB-HID reports → it's the
  *gadget*.

Each board does both; it just exercises whichever applies. Both enumerate as the
same composite **HID + CDC + MSC** device (`Bake-Ware "Geppetto"`); the idle
interface on each side is harmless.

### Wire frame

Identical on the CDC link and the UART link, so the bridge is a dumb byte pipe:

```
0xAB | type | len | payload[len] | crc8(type,len,payload)     crc8 poly 0x07, init 0
  type 1 = keyboard (8 bytes: mods, reserved, key[6])
  type 2 = mouse    (7 bytes: buttons, x:i16le, y:i16le, wheel:i8, pan:i8)
  type 4 = consumer (2 bytes: usage:u16le)
```

## Build & flash the firmware

Needs [PlatformIO](https://platformio.org/) and, on the build host, `dosfstools`
(`mkfs.fat`), `mtools` (`mcopy`), and `xxd` — the build embeds the host client as
a small FAT image (see `firmware/tools/`).

```sh
cd firmware
pio run                         # builds .pio/build/pico/firmware.uf2
```

Flash both boards. Hold **BOOTSEL** while plugging a Pico in (it mounts as
`RPI-RP2`), then copy the UF2:

```sh
cp .pio/build/pico/firmware.uf2 /run/media/$USER/RPI-RP2/
```

Already-flashed boards can be reflashed without the BOOTSEL button: a 1200-baud
"touch" on the CDC port resets them into the bootloader (`pio run -t upload`
does this automatically).

> Uses the **earlephilhower** Arduino-Pico core via the
> `maxgerhardt/platform-raspberrypi` fork (the stock `raspberrypi` platform only
> ships the Arduino-Mbed core). USB stack is Adafruit TinyUSB.

## Install

One command installs the deps, grants device access (the `input`/`uucp` groups),
and adds a tray launcher — then **log out and back in** for the groups to apply:

```sh
cd client
./install.sh --autostart        # omit --autostart to skip the login-start entry
```

On Arch/CachyOS it installs the packages for you; elsewhere it lists them and you
add yourself to the groups (`sudo usermod -aG input,uucp "$USER"`) + install
PyGObject/GTK3/GTK4/python-evdev/pyserial. No sudo is needed to *run* it after.

## Run the host client

```sh
cd client
./run.sh                        # auto-detects the bridge Pico's serial port
```

1. Leave one Pico in your PC (becomes the bridge), plug the other into the target.
2. Run the client on your PC.
3. **Double-tap Right-Ctrl** (default) to start driving the target; do it again to
   come back. While forwarding, the selected keyboard/pointer are grabbed
   (`EVIOCGRAB`) so they stop affecting the local machine.

The client also rides along on the bridge's read-only USB drive (`geppetto.py`,
`geppetto_config.py`, the GUI, launchers), so you can pull it off the dongle on a
fresh machine.

## Settings GUI

A small GTK4 app picks which devices get forwarded and lets you set the hotkey:

```sh
cd client
./run_gui.sh                    # runs as your user (needs the 'input' group)
```

- **Devices**: tick the keyboards/pointers to forward. (No selection saved yet =
  forward everything, the default.)
- **Hotkey**: click *Capture* and press the combo you want — a single key/button
  becomes a **double-tap** (or **single tap** via the switch); several keys held
  together become a **chord** (press once). Mouse buttons work too.
- **Save** writes `~/.config/geppetto/config.json` and applies it **live** to any
  running client (no restart) by signalling it.

The hotkey is watched on every device regardless of selection, so it always
works. Needs PyGObject + GTK4 (`python-gobject`, `gtk4`) and the `input` group.

## Tray indicator

A system-tray indicator (GTK3 + Ayatana AppIndicator) shows forwarding state and
controls the bridge — works on any StatusNotifier tray (KDE, GNOME w/ extension,
waybar, etc.):

```sh
cd client
./run_tray.sh                   # marionette icon appears in your tray
./install-desktop.sh            # optional: app-menu launcher (+ --autostart)
```

- Icon: **grey** = client not running · **white** = running, forwarding off ·
  **green** = forwarding on.
- Menu: open Settings, toggle Forwarding, start/stop the bridge client, quit.
  Middle-click the icon to toggle forwarding.

The client publishes its state to `$XDG_RUNTIME_DIR/geppetto.status` and toggles
on `SIGUSR1`; the tray polls the former and sends the latter. Needs
`libayatana-appindicator` + `gtk3`.

> Run everything as **your user**, not via sudo — a tray icon only appears on
> your own session bus (a root app won't register with the desktop's tray). The
> `input`/`uucp` groups above are what let it read devices without root.

## Layout

```
firmware/   PlatformIO project (one .cpp, symmetric). tools/ builds the embedded
            USB-drive image. src/disk_image.h is generated — not committed.
client/     geppetto.py         evdev capture -> framed HID over serial
            geppetto_config.py  shared config, device/hotkey/status helpers
            geppetto_gui.py     GTK4 settings app (device + hotkey picker)
            geppetto_tray.py    GTK3 tray indicator (state + controls)
            icons/              tray/app icons
            run*.sh             launchers; install-desktop.sh adds a .desktop
```

## License

MIT — see [LICENSE](LICENSE).
