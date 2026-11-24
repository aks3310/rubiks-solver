# Wiring

> **Proposed wiring - check before you power up.** The UART pin positions
> below follow Marlin's MKS Gen L / RAMPS pin file. Confirm the PDN_UART pin
> location against the BIGTREETECH TMC2209 v1.2/v1.3 manual for your exact
> module before connecting anything.

## Motors and drivers

This matches the pin numbers in `firmware/cube_firmware/config.h`, which decide
which socket drives which face. There is no motor on the top (U, white) face.

| Socket | Face | STEP | DIR | EN | UART TX | UART RX | Header |
| --- | --- | --- | --- | --- | --- | --- | --- |
| X | D (down) | D54 | D55 | D38 | D16 (TX2) | D17 (RX2) | EXP1 |
| Y | L (left) | D60 | D61 | D56 | D59 | D64 | AUX-2 |
| Z | F (front) | D46 | D48 | D62 | D42 | D65 | AUX-2 |
| E0 | B (back) | D26 | D28 | D24 | D44 | D66 | AUX-2 |
| E1 | R (right) | D36 | D34 | D30 | D40 | D63 | AUX-2 |

The Z socket on the MKS Gen L has two motor connectors in parallel; use only one.

The drivers currently run in **standalone mode** (no UART wires, no MS jumpers,
current set with Vref), so the UART columns are unused. They list the pins the
firmware would use for each face if UART were wired; each pair must then go to
the driver in that face's socket. To move a face to another socket, change its
`*_STEP`, `*_DIR` and `*_EN` numbers in `config.h` and the `SOCKET` map in
`pi/main.py`.

## TMC2209 single-wire UART (per driver)

```
 MKS TX pin ──[ 1 kΩ ]──┬── driver PDN_UART pin
 MKS RX pin ────────────┘
```

* Remove **all** MS1/MS2/MS3 jumpers under the drivers. In UART mode MS1/MS2
  are address pins; with no jumper the driver's internal pull-downs give
  address 0, which is what the firmware expects (each driver has its own
  wire pair, so they can all be address 0).
* On many MKS Gen L + TMC2209 builds the PDN_UART signal is reached from the
  driver-side pin of the MS3 jumper header. Confirm on your module first.
* After wiring, run `python main.py drivers`; each line must say `ok`.

## Power

```
230 V AC ─ SMPS 24 V 5 A ─┬─ 5 A fuse ─ MKS Gen L power input (12–24 V)
                          └─ buck converter (set to 5.1 V first!) ─ Pi 5 5 V
Pi 5 USB-A ───────────── USB-B ─ MKS Gen L (CH340 → /dev/ttyUSB0)
```

* Raspberry Pi 5: the buck converter must deliver **5 A** at 5.1 V. Below
  that, the Pi limits USB current and shows a power warning.
* Never plug or unplug a motor while the board is powered.

## Camera

Pi Camera Module 3 Wide on the Pi 5's CAM/DISP port with the **22-pin to
15-pin** cable (Pi 5 uses the smaller connector). It is mounted at the
back-bottom-right corner of the cube, so it sees the bottom (D), back (B) and
right (R) faces; their centres are hidden behind the motor adapters.
