# Software setup guide

From a blank SD card to a full solve. Follow the parts in order; each one ends
with a check, so you know it worked before moving on.

| Part | Where | Time |
| --- | --- | --- |
| 1. Get the code | Laptop | 5 min |
| 2. MKS firmware | Laptop (Arduino IDE), USB to the MKS | 15 min |
| 3. Raspberry Pi OS | Laptop + Pi | 20 min |
| 4. Python environment | Pi | 15 min |
| 5. Pi to MKS link | Pi | 10 min |
| 6. Camera and lighting | Pi | 20 min |
| 7. Calibration | Pi (desktop / VNC) | 20 min |
| 8. First solve | Pi | 5 min |
| 9. Speed tuning, overshoot and shake | Pi | 20 min |
| 10. Dashboard | Laptop (+ Pi) | 10 min |

Hardware assumed: Raspberry Pi 5, Camera Module 3 Wide at the back-bottom-right
corner of the cube (it sees the D, B and R faces), MKS Gen L v1.0, five BTT
TMC2209 v1.3 in standalone mode (no MS jumpers = 1/8 microstepping, current set
with Vref), five NEMA 17 motors on the F, R, B, L and D faces. There is **no
motor on the top (U, white) face**: every U turn is replaced by turns of the
other five faces. See `docs/wiring.md`.

Which MKS socket drives which face is set by the pin numbers in
`firmware/cube_firmware/config.h`. With the current file:

| Face | F | R | B | L | D |
| --- | --- | --- | --- | --- | --- |
| MKS socket | Z | E1 | E0 | Y | X |

---

## 1. Get the code

Everything is in this repository:

```
pi/              Pi program (main.py) - scan, solve, drive the robot
firmware/        MKS Gen L sketches (cube_firmware + test sketches)
docs/            wiring, serial protocol, this guide
laptop_server/   dashboard (part 10)
raspberry_pi/    Pi solver API for the dashboard (part 10)
server/          older laptop-only solver API (not needed)
```

Keep the repository on your laptop (you upload the firmware from there) and
copy it to the Pi in part 4.

---

## 2. MKS firmware (on the laptop)

1. Install the **Arduino IDE** (2.x).
2. The MKS Gen L uses a **CH340** USB chip. If Windows does not show a new COM
   port when you plug the board in, install the CH340 driver.
3. Arduino IDE → Library Manager → install **TMCStepper** (by teemuatlut).
   It is needed to compile even in standalone mode.
4. Open `firmware/cube_firmware/cube_firmware.ino`.
5. Tools → Board: **Arduino Mega or Mega 2560**, Processor: **ATmega2560**,
   Port: the MKS's COM port. Click Upload.

**Check:** open the Serial Monitor (115200 baud, line ending **Newline**). After
a reset it prints `READY rubik-fw 1.3`. Type `PING` → `PONG` and `OK`.

Firmware **1.3** is needed by the Pi program: it adds the `OVERSHOOT` and
`SHAKE` commands (part 9). With older firmware `main.py` stops and asks you to
upload `cube_firmware` again.

### Check every motor's direction

With the cube **out** (or the adapters loose), type each line and watch:

```
MOV F
MOV R
MOV B
MOV L
MOV D
```

Each face must turn **90° clockwise as seen from outside that face**. If the
wrong motor moves, fix the face's `*_STEP`, `*_DIR` and `*_EN` pins in
`config.h`. If it turns the wrong way, set its flag (for example
`#define F_INVERT true`). Upload again after any change. If a motor turns 45°
or 180° instead of 90°, the MS jumpers are not what the firmware expects:
remove all MS1/MS2/MS3 jumpers (1/8 microstepping, 400 microsteps = 90°).

After a reset the board does not overshoot (`OVERSHOOT 0`), so these test turns
are exactly 90°. The Pi program sets overshoot every time it connects.

### Standalone-mode notes

- `cube_firmware` was written for UART mode but works in standalone mode:
  microstepping comes from the jumpers (none = 1/8, which matches the firmware)
  and current from the Vref screw (about 1.9–2.0 V for your motors).
- `DRV` will report `FAIL` for every driver and `CUR` has no effect. That is
  expected without UART wires.

### Test sketches (optional)

`firmware/motor_test` (type `X 400`), `firmware/speed_test` (find the top speed)
and `firmware/stepper_test` are separate programs. Uploading one **replaces**
`cube_firmware`, so upload `cube_firmware` again before using the Pi software.

---

## 3. Raspberry Pi OS

1. Use **Raspberry Pi Imager** → Raspberry Pi 5 → **Raspberry Pi OS (64-bit)**
   (the desktop version, not Lite: calibration opens a window).
2. In Imager's settings set a hostname, username and password, and
   **enable SSH**.
3. Boot the Pi. If the status LED is red, press the power button once.

### Connect over Ethernet

Plug a cable from the Pi to the laptop. On the laptop: Settings → Network →
Advanced network settings → More network adapter options → right-click Wi-Fi →
Properties → Sharing → allow sharing with the Ethernet adapter. The Pi then
gets an address like `192.168.137.x` and internet access (needed for installs).

Find it with `arp -a` in PowerShell (or try the hostname), then:

```bash
ssh pi@192.168.137.204
```

(use your own username and address).

### Turn on VNC and check the camera

```bash
sudo raspi-config
```

Interface Options → VNC → Yes. Connect from the laptop with **RealVNC Viewer**.

```bash
rpicam-hello --list-cameras
```

**Check:** it lists an `imx708_wide` camera. If it lists nothing, power off and
reseat the camera ribbon (Pi 5 needs the 22-pin to 15-pin cable).

---

## 4. Python environment (on the Pi)

Copy the repository from the laptop (PowerShell, in the folder that contains
`rubiks-solver`):

```bash
scp -r .\rubiks-solver pi@192.168.137.204:~/
```

Then on the Pi:

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-opencv python3-numpy python3-scipy python3-dev libffi-dev build-essential
cd ~/rubiks-solver/pi
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- `--system-site-packages` lets the virtual environment use the camera library,
  OpenCV and numpy from `apt`.
- Do **not** `pip install numpy` or `opencv-python` into the venv: a second copy
  of numpy breaks picamera2.
- Run `source ~/rubiks-solver/pi/.venv/bin/activate` in every new terminal.

**Check:**

```bash
python -m pytest -q
python main.py simulate -n 20
```

Expect `52 passed` (it takes about a minute: some tests search scan plans) and
`20/20 solved in simulation`. The first solve builds Kociemba's tables (a few
seconds to a minute); later solves are fast.

---

## 5. Pi to MKS link

Plug the MKS into a Pi USB port.

```bash
ls /dev/ttyUSB*
groups
```

You should see `/dev/ttyUSB0`, and `dialout` in your groups. If not:
`sudo usermod -aG dialout $USER`, then log out and back in. If the port has
another name, change `port` in `pi/config.toml`.

**Check (cube out or adapters loose):**

```bash
python main.py ping          # READY rubik-fw 1.3 | ping ok
python main.py move "F F'"   # front face out and back
python main.py jog F 10      # nudge the front motor 10 microsteps
python main.py shake         # all faces wiggle and stop where they started
python main.py motors off    # release all motors
python main.py motors on     # hold them again
```

`move` sends the moves exactly as typed (no `U`: there is no top motor). Use
`jog` to line every adapter up square with the frame before loading the cube
(400 microsteps = 90°). The motors hold position while powered, so jog after
every power-up if the faces have drifted.

---

## 6. Camera and lighting

Colour reading depends on stable light more than anything else.

1. **Enclose the robot** (matte black card or a box) so window light cannot reach
   the cube. Daylight changes break the colour calibration.
2. **Light it yourself** with white LEDs behind a diffuser (paper or frosted
   acrylic), mounted on the walls, not next to the lens.
3. For glossy cubes, **cross-polarise**: polarising film over the lights and over
   the lens, lens film rotated until the highlights vanish.
4. Look at the live picture over VNC: `rpicam-hello -t 0`. Close it with Ctrl+C
   **before** running `main.py` (only one program can use the camera).
5. Optional: set a fixed focus in `pi/config.toml` →
   `lens_position = 1 / distance in metres` (for example `6.5` for about 15 cm).
   Without it, the camera autofocuses once at the start of each run.

The program locks exposure, white balance and focus after a 2-second warm-up,
so the light must be on before you start it.

Stickers that still show glare (on this robot: two opposite edge stickers on
each of D, B and R) do not need to be fixed with lighting: you skip them in
part 7a and the scan reads them from another position.

---

## 7. Calibration (once, and again after moving the camera or lights)

**Load the cube the same way every time:** white on top, green at the front.
The camera then sees yellow (D), blue (B) and red (R). The centres are never
read, so this is how the program knows which colour belongs to which face.

### 7a. Sticker positions

Run this from the Pi **desktop** (VNC), not plain SSH, because it opens a window:

```bash
cd ~/rubiks-solver/pi && source .venv/bin/activate
python main.py calibrate-rois
```

It takes a photo and asks for each sticker in turn (`D1`, `D2`, …, `R9`; the
hint at the top says which face and corner/edge). Left-click on the **visible
part** of that sticker, away from shafts and highlights. Press `s` for every
sticker that cannot be read reliably (glare, hidden by a rod).

| Key | Action |
| --- | --- |
| click | set this sticker |
| `s` | sticker cannot be read (skip) |
| `u` | undo the last one |
| `q` | finish and save |

It saves `data/rois.json` and a reference photo `data/rois.jpg`.

**How skipped stickers are handled.** Between photos the robot turns faces so
that every sticker passes through a readable position at least once. Every turn
is known exactly, so each reading is traced back to where that sticker was in
the scramble. The planner uses the fewest photos possible:

| Readable stickers | Photos | Turns between photos (total) |
| --- | --- | --- |
| all 24 on D, B, R | 2 | 10 |
| two opposite edges skipped on each face (18 readable) | 4 | 13–14 |

Four is the minimum in the second case: a turn never moves an edge sticker to a
corner position, and each photo then shows only 6 of the 24 edge stickers.

The first command that scans searches for the plan (about 15 s) and saves it to
`data/scan_plans.json`; later runs load it instantly. If you redo `calibrate-rois`
with different stickers, a new plan is searched automatically. Every scanning
command prints the plan it uses:

```
scan plan: 4 photos, 13 moves: (as loaded) | L B F' D | B F' R D L | F' R D L
```

### 7b. Colours

Load the cube white up, green front. It can be **solved or scrambled**:

```bash
python main.py calibrate-colours              # cube must be solved
python main.py calibrate-colours --scrambled   # any scramble
```

Calibration runs slowly so you can watch every turn (`[calibration]` in
`pi/config.toml`): it waits for a **key press before each turn**, pauses 1 s
after it, and never turns opposite faces together. Check each face lands
square before pressing the next key. Ctrl+C stops the scan; the turns already
made are then undone, again one key press per turn (Ctrl+C a second time leaves
the cube where it is).

It scans, then **turns the cube back exactly as you loaded it**, then saves one
reference colour per face to `data/colours.json` and prints the three closest
colour pairs. With `--scrambled` it also prints the cube it detected: check it
matches the real cube.

**Check:** the closest pair should be well above 15. A warning means two faces
look too similar (usually red/orange or yellow/green on a stickerless cube):
improve the lighting and run it again.

---

## 8. First solve

Scramble the cube, load it white up / green front, then:

```bash
python main.py solve --dry-run
```

This scans and solves but only prints: the detected cube as a net, the three
least certain stickers, the solver's moves and the robot's steps. Compare the
net with the real cube.

When the detected cube is right:

```bash
python main.py solve
```

What happens:

1. **Scan (not timed):** photo, turns, photo … (4 photos with skipped edge
   stickers), then **every scan move is undone**, so the cube is back in exactly
   the scramble you gave it. If the scan stops early (error, Ctrl+C) the moves
   made so far are undone; if even that fails, the moves to do by hand are printed.
2. **Software (timed):** the photos are turned into the original scramble,
   colours are named (exactly 8 stickers per colour), and Kociemba solves it.
   The 24 ways of holding the cube are tried (up to `time_budget_s`), every U
   turn is rewritten as `Y D Y` (`Y = R L' F2 B2 R L'`), opposite faces that
   follow each other turn together (`R+L'`), and 180° turns become two 90°
   turns. The plan is checked on a cube model before anything moves.
3. **Physical (timed):** the whole solution goes to the MKS in one line.

The output ends with:

```
scan 9.80 s (not timed; 13 scan moves, then undone)
software  0.420 s   (colours + solver, target < 2 s)
physical  2.300 s   (firmware-measured motion, target < 2 s)
after-scan total incl. USB 2.760 s
```

(example numbers). Photos of every run are saved in `pi/logs/<date-time>/`.
If the colours do not form a real cube, it stops after the scan (the cube is
already back in its scramble): check the photos and the lighting.

---

## 9. Speed tuning, overshoot and shake

### Speed

Find the fastest settings that never skip a step. Cube loaded, then:

```bash
python main.py bench --speed 6000 --accel 150000
python main.py bench --speed 8000 --accel 250000
python main.py bench --speed 10000 --accel 300000
```

Each run does 20 random moves and then their inverse, so the cube must end
exactly where it started. Keep going up until it does not, then back off about
15 % and put the values in `pi/config.toml` → `[motion]`. If turns skip while
speeding up, lower `--accel` rather than `--speed`. Keep the drivers cooled;
check motor and driver temperature after a few minutes.

### Overshoot

Every turn goes a little past 90° in its direction, then comes back, which helps
a stiff cube's layer seat fully (`[motion]` in `pi/config.toml`):

```toml
overshoot = true      # false = exactly 90°
overshoot_deg = 10    # 0-30; 10° = 44 microsteps
```

It applies to every move (solve, scan, calibration, `move`, `bench`) but never
to `jog`. It costs a few milliseconds per turn: run `bench` with it on and off
and keep it only if it prevents jams.

### Shake

All five faces wiggle back and forth together and stop exactly where they
started, to settle a cube that is slightly out of line:

```bash
python main.py shake                                   # settings from config.toml
python main.py shake --deg 5 --cycles 10 --faces FB    # try other values
```

```toml
[shake]
amplitude_deg = 3     # 0.5-15
cycles = 5            # 1-50
faces = "FRBLD"
before_scan = false   # shake once before the first photo (not timed)
before_solve = false  # shake after the scan, before the solution (timed)
```

Start with 2–4°. Shaking settles layers that are a few degrees off; it does not
fix a face that is far off (use `jog`).

---

## 10. Dashboard

A web page with a 3D cube, scrambles and solutions. It runs on the laptop and
asks the Pi to solve (`raspberry_pi/solver_app.py`, port 8011).

**On the Pi** (needs the bundled solver in `raspberry_pi/vendor/pure_python`):

```bash
cd ~/rubiks-solver/raspberry_pi
bash setup.sh
./run_solver.sh
```

**On the laptop** (PowerShell, in the `rubiks-solver` folder):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r laptop_server\requirements.txt
$env:PI_SERVER_URL = "http://192.168.137.204:8011"
Set-Location .\laptop_server
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010
```

Open http://localhost:8010. The top right shows **Connected** when the Pi
answers.

What it does:

- **The top (white) face cannot be turned**, as on the robot: clicking it only
  shows a note, and there are no U buttons. Random and custom scrambles use only
  F, R, B, L and D (a custom scramble with U is refused).
- **Move order** (under the move buttons) lists every move made on the 3D cube
  in order: scrambles, move buttons, clicks and applied solutions. **Copy**
  copies it.
- **Display state** loads a 54-letter state (URFDLB order); **Upload state** and
  **Read Pi scan** store and load a state on the Pi.
- **Find solutions** returns routes that use only F, R, B, L and D (U turns
  rewritten), ranked by estimated time or turn count; **Apply solution** plays
  one on the 3D cube.

Without the Pi, the page still works: Upload and Read keep the state on the
laptop, and the top right says *Raspberry Pi unavailable*. Solving then needs
the bundled solver folder on the laptop (`raspberry_pi/vendor/pure_python`);
without it, Find solutions says *Solver offline*.

The dashboard's routes rewrite U turns but do not try the 24 orientations, so
they can be longer than what `main.py solve` runs. Use `main.py solve` for
timed runs. `raspberry_pi/robot_executor.py` can send a dashboard solution to
the MKS (`--execute --port /dev/ttyUSB0`; dry run by default).

---

## Troubleshooting

| Problem | Likely cause | Fix |
| --- | --- | --- |
| No `READY` in the Serial Monitor | Wrong board/port, or line ending | Board Mega 2560, correct COM port, 115200 baud |
| `no READY from the board` on the Pi | Wrong port, or a test sketch is loaded | `ls /dev/ttyUSB*`; upload `cube_firmware` again |
| `the board firmware has no OVERSHOOT/SHAKE command` | Firmware older than 1.3 | Upload `firmware/cube_firmware` again |
| `Permission denied: /dev/ttyUSB0` | Not in `dialout` | `sudo usermod -aG dialout $USER`, log in again |
| The wrong motor moves | Pin numbers in `config.h` | Fix that face's `*_STEP/_DIR/_EN` pins |
| A face turns 45° or 180° | MS jumpers fitted | Remove all MS jumpers (1/8) |
| A face turns the wrong way | Motor direction | Set its `*_INVERT` flag in `config.h` |
| Motor buzzes but does not turn | Too fast, too little current, or cube too tight | Lower speed/accel, Vref about 1.9 V, loosen the cube |
| Cube jams during the scan | A face stopped off-square | `jog` the adapters square, lower speed/accel, try `shake` and overshoot, loosen the cube |
| `Could not turn the cube back` | The undo after a scan failed | Do the printed moves by hand or with `move` |
| First scan pauses ~15 s before moving | Scan plan search (once) | Normal; saved in `data/scan_plans.json` |
| `Camera __init__ sequence did not complete` | Another program has the camera | Close `rpicam-hello` / `rpicam-vid` |
| `calibrate-rois` shows no window | Running over plain SSH | Run it from the desktop (VNC) |
| `not a valid cube` after scanning | A sticker read wrong | Check `pi/logs/<time>/` photos, lighting, sticker points (skip glare stickers with `s`); recalibrate |
| Red and orange mixed up | Glare or changing daylight | Enclose the robot, diffuse the light, redo `calibrate-colours` |
| `pip install kociemba` fails | Missing build tools | `sudo apt install python3-dev libffi-dev build-essential` |
| picamera2 import error inside the venv | numpy installed with pip | `pip uninstall numpy`; venv made with `--system-site-packages` |
| Dashboard says *Raspberry Pi unavailable* | Pi solver not running or wrong address | `./run_solver.sh` on the Pi; set `PI_SERVER_URL` to the Pi's address |

## Command reference (on the Pi, in `~/rubiks-solver/pi`)

| Command | Does |
| --- | --- |
| `python main.py simulate` | Whole pipeline without hardware |
| `python main.py ping` | Check the MKS link |
| `python main.py move "R L' F2"` | Turn faces exactly as typed (no U) |
| `python main.py jog F 10` | Nudge one motor (microsteps, no overshoot) |
| `python main.py shake [--deg --cycles --faces]` | Wiggle the faces to settle the cube |
| `python main.py motors on` / `off` | Hold or release all motors |
| `python main.py calibrate-rois` | Click sticker positions, `s` to skip (desktop) |
| `python main.py calibrate-colours [--scrambled]` | Record face colours, one turn per key press |
| `python main.py solve --dry-run` | Scan + solve, print only (cube turned back) |
| `python main.py solve` | Scan, turn back, solve and execute |
| `python main.py bench [--speed --accel]` | Time the motors for speed tuning |
| `python -m pytest -q` | Run the unit tests |
