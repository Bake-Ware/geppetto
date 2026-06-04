# Geppetto firmware

Symmetric firmware for the two soldered RP2040 Picos. One `.cpp`; see the
[top-level README](../README.md) for the full picture.

## Build

```sh
pio run                 # -> .pio/build/pico/firmware.uf2
```

Build-host deps: `dosfstools` (mkfs.fat), `mtools` (mcopy), `xxd`. The pre-build
hook (`tools/prebuild.py` → `tools/mkdisk.sh`) packs `../client` into a small
read-only FAT12 image and emits `src/disk_image.h`, which the firmware serves
over USB MSC. That header is generated on every build and is git-ignored.

## Flash

- First time / recovery: hold **BOOTSEL**, plug in (mounts as `RPI-RP2`), then
  `cp .pio/build/pico/firmware.uf2 /run/media/$USER/RPI-RP2/`.
- Already running Geppetto: `pio run -t upload` (1200-baud touch drops it into
  the bootloader automatically).

Flash the **same** UF2 to both boards.

## Notes / gotchas

- Must use the earlephilhower core via `platform =
  https://github.com/maxgerhardt/platform-raspberrypi.git`. The stock
  `raspberrypi` platform only ships the Arduino-Mbed core and silently ignores
  `board_build.core = earlephilhower`.
- Don't redefine `CFG_TUD_HID` / `CFG_TUD_CDC` / `CFG_TUD_MSC` — the core's
  `tusb_config.h` sets them. Just `-DUSE_TINYUSB` + `board_build.usbstack =
  tinyusb`.
- The core's `SingleFileDrive` library can't be used here: it `#error`s under
  `USE_TINYUSB`, which we need for the custom composite HID. Hence the
  embedded-FAT + `Adafruit_USBD_MSC` approach.
