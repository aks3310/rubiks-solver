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
| 10. Robot dashboard | Pi, opened from any browser | 10 min |

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
pi/web_app.py    robot dashboard hosted on the Pi (part 10), page in pi/web/
firmware/        MKS Gen L sketches (cube_firmware + test sketches)
docs/            wiring, serial protocol, this guide
laptop_server/   older laptop dashboard (optional, end of part 10)
raspberry_pi/    Pi solver API for the older laptop dashboard
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
a reset it prints `READY rubik-fw 1.4`. Type `PING` → `PONG` and `OK`.

Firmware **1.4** is needed by the Pi program: 1.3 added the `OVERSHOOT` and
`SHAKE` commands (part 9), and 1.4 makes pauses longer than 16 ms (`SETTLE`)
work correctly. With older firmware `main.py` stops and asks you to
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

Expect `82 passed` (it takes about a minute: some tests search scan plans) and
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
python main.py ping          # READY rubik-fw 1.4 | ping ok
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

Stickers that still show glare (on this robot: the two far edge stickers on
each of D, B and R) do not need to be fixed with lighting: they are left out of
the sticker positions (part 7a) and the scan reads them from another position.

---

## 7. Calibration (once, and again after moving the camera or lights)

**Load the cube the same way every time:** white on top, green at the front.
The camera then sees yellow (D), blue (B) and red (R). The centres are never
read, so this is how the program knows which colour belongs to which face.

To load it another way, say so in `pi/config.toml`:

```toml
[cube]
top = "white"     # colour facing up (the face with no motor)
front = "green"   # colour facing the front
```

The other four faces follow from the standard colour scheme (white–yellow,
green–blue and red–orange are opposite); for example `top = "yellow"`,
`front = "green"` puts orange on the right. The colour calibration remembers
the orientation it was made with and is remapped automatically, so changing
`[cube]` does not need a new `calibrate-colours`. The printed cube and the
dashboard use these colours too.

**Face turns cannot move the cube onto other motors.** Each motor holds a
centre, and no sequence of face turns moves a centre, so a cube loaded with the
wrong colours on the motors cannot be fixed by turning faces. Either take the
cube out and load it correctly, or set `[cube]` to the way it is loaded.

### 7a. Sticker positions

**Already done for this robot: nothing to click.** The camera is fixed, so the
position of every face is hard-coded in `pi/data/face_model.json` (the face
model, `pi/rubik/face_model.py`):

- the 7 face corners (T, V, BL, BR seen in the photo; R1, B3, D1 behind the
  rods) and the lens bend, fitted so the grooves between cubies fall on each
  face's 3×3 grid (about 3 px, see `docs/face_quads.jpg`);
- each sticker is read from the **middle 40 %** of its cell (`patch_inset = 0.3`
  in `[scan]`), away from grooves and bevels; blown-out pixels (reflections)
  are ignored;
- positions are stored for a 2000×1125 photo and scaled to the camera's
  resolution.

The stickers read are listed in `[scan] visible` (15 of the 21 the camera sees):

| Face | Read | Not read |
| --- | --- | --- |
| R (red) | R3 R6 R7 R8 R9 | R2 R4 (far edges, glare), R1 (behind the rod) |
| B (blue) | B1 B4 B7 B8 B9 | B2 B6 (far edges, glare), B3 (behind the rod) |
| D (yellow) | D3 D6 D7 D8 D9 | D2 D4 (far edges, glare), D1 (behind the rod) |

The other faces are read by turning them into view (below).

**Check it on the robot** (cube loaded, light on):

```bash
python main.py show-faces            # or: --image photo.jpg
```

It saves `pi/data/faces_check.jpg` with the curved face outlines, the sticker
grid and a white box where each sticker is sampled. The lines must sit on the
grooves and every box inside one sticker.

If the whole grid is too small or too big (the camera picture is zoomed
differently from the photo the model was fitted on), first try the full sensor:
`[camera] full_fov = true`. Otherwise scale and move the grid until it fits,
then put the values in `pi/config.toml` → `[scan]`:

```bash
python main.py show-faces --scale 1.3              # grow the grid about the centre
python main.py show-faces --scale 1.3 --dx 10 --dy -5   # then move it (camera pixels)
```

```toml
face_scale = 1.3
face_offset = [10, -5]
```

The current values are written at the top of `faces_check.jpg`. If the grid's
shape no longer fits (the camera was moved or tilted), the face model has to be
refitted (or switch to clicked points: below).

**Clicked points instead** (`[scan] sticker_source = "rois"`): the older method,
`pi/data/rois.json` (from the TA6 coordinate guide, or `calibrate-rois`).

To click them again, run this from the Pi
**desktop** (VNC), not plain SSH, because it opens a window:

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
| **this robot:** 15 (far edges and far corners not read) | 4 | 12 |

Four is the minimum here: a turn never moves an edge sticker to a corner
position, and each photo shows only 6 of the 24 edge stickers.

The plan is saved in `data/scan_plans.json` (already there for this robot), so
it loads instantly. If you redo `calibrate-rois` with different stickers, a new
plan is searched automatically (up to about 15 s, once). Every scanning command
prints the plan it uses:

```
scan plan: 4 photos, 12 moves: (as loaded) | R B' F' R | R F' D B' | L' D B R'
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
scan 9.80 s (not timed; 12 scan moves, then undone)
software  0.420 s   (colours + solver, target < 2 s)
physical  2.300 s   (firmware-measured motion, target < 2 s)
after-scan total incl. USB 2.760 s
```

(example numbers). Photos of every run are saved in `pi/logs/<date-time>/`.

**Timing settings** (`pi/config.toml`):

| Setting | Pause | Applies to |
| --- | --- | --- |
| `[scan] turn_delay_s` | after each scan turn and each undo turn (Pi side) | scan only, not timed; 0 = none |
| `[camera] settle_s` | before each photo | scan only |
| `[motion] settle_us` | after **every** move, done by the board (0–2 000 000 µs) | scan **and** the timed solution |
| `[calibration] turn_delay_s` | after each turn, plus a key press | `calibrate-colours` only |

To watch the scan turn by turn, set `[scan] turn_delay_s = 0.5`; the solution
stays at full speed.
If the colours do not form a real cube, it stops after the scan (the cube is
already back in its scramble): check the photos and the lighting.

### Read and solve separately

```bash
python main.py read                     # scan only: prints the cube and its 54-letter state
python main.py solve --state <54 letters>   # solve that state without scanning again
```

`read` ends with the cube back in its scramble, like every scan. Use
`solve --state` only if the cube has not been turned since it was read. The
dashboard (part 10) uses these two commands.

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

## 10. Robot dashboard (hosted on the Pi)

A web page served by the Pi that drives the robot: scramble the real cube, read
it with the camera and solve it, with a 3D cube that follows along. Open it
from any browser on the same network (laptop or phone). Every button runs
`main.py`, so it uses the same `config.toml`, speed, overshoot, scan plan and
solver as the command line. More detail: `docs/web_ui.md`.

**Start it** (on the Pi):

```bash
cd ~/rubiks-solver/pi
./run_ui.sh
```

It uses the `.venv` from part 4. Open `http://<pi-ip>:8080` on the laptop
(for example `http://192.168.137.204:8080`; find the address with
`hostname -I` on the Pi). The top right says **Robot ready**.

**The three buttons:**

| Button | Runs | What you see |
| --- | --- | --- |
| 1 Scramble | random F/R/B/L/D moves, then `main.py --json move "<moves>"` | the moves on screen; the robot turns them, then the 3D cube does |
| 2 Read cube | `main.py --json read` | the scan (moves undone afterwards); the state read on the 3D cube, the 54 letters and the least certain stickers |
| 3 Solve cube | `main.py --json solve --state <last read>` | the solver's moves, the robot steps, and scan / software / motion times; the 3D cube turns to solved |

- **Solve uses the last Read** if the cube has not been scrambled since. After a
  Scramble, Solve scans the cube first (the times show the scan).
- **Load the cube as set in `[cube]` in `config.toml`** (white on top, green at the front by default), as for every run. The page shows the colours it expects.
- **The top (white) face is fixed:** scrambles never use U, and the 3D cube can
  only be turned by these buttons (drag it to look around).
- **One command at a time:** while one runs, the buttons are disabled and a
  second browser gets *the robot is busy*. The output of each `main.py` run is
  shown under **main.py output**, including any error.
- Each button opens the USB link fresh, which restarts the MKS (about 2 s), and
  Read starts the camera fresh. This is outside the timed part of a solve.
- **Do not run `main.py` robot commands by hand while a button is running**:
  both need the USB link and the camera.

**Start it automatically when the Pi boots** (edit `User` and the paths in
`pi/rubik-ui.service` first if your username or folder differ):

```bash
cd ~/rubiks-solver/pi
sudo cp rubik-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rubik-ui
journalctl -u rubik-ui -f        # its output; Ctrl+C to stop watching
sudo systemctl stop rubik-ui     # stop it (for example to use main.py by hand)
```

**Try the page without the robot** (any computer, in `pi/`):

```bash
python web_app.py --fake-robot
```

Open `http://localhost:8080`. A pretend robot (a cube in memory, starting
solved) answers instead of `main.py`.

**Check:** with the cube loaded and the light on, press **Read cube**: the 3D
cube must match the real cube. Then **Scramble** and **Solve cube**.

### Older laptop dashboard (optional)

`laptop_server/` is the earlier dashboard: it runs on the laptop and only shows
a 3D cube and solutions (it cannot turn the motors). It asks
`raspberry_pi/solver_app.py` (port 8011) on the Pi to solve, which needs the
bundled solver in `raspberry_pi/vendor/pure_python`.

```bash
cd ~/rubiks-solver/raspberry_pi && ./run_solver.sh        # on the Pi
```

```powershell
# on the laptop, in the rubiks-solver folder
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r laptop_server\requirements.txt
$env:PI_SERVER_URL = "http://192.168.137.204:8011"
Set-Location .\laptop_server
..\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8010
```

Open http://localhost:8010. As on the robot, the top face cannot be turned and
its solutions use only F, R, B, L and D, but it does not try the 24
orientations, so routes can be longer than what `main.py solve` runs. It also
has the **Move order** list (with **Copy**), Display / Upload state and Read Pi
scan. Without the Pi it keeps uploaded states on the laptop, and Find solutions
needs the bundled solver folder there too (otherwise it says *Solver offline*).
`raspberry_pi/robot_executor.py` can send one of its solutions to the MKS
(`--execute --port /dev/ttyUSB0`; dry run by default).

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
| Robot dashboard page does not open | `run_ui.sh` not running, or wrong address | `./run_ui.sh` (or `sudo systemctl status rubik-ui`); use the Pi's IP with `:8080` |
| Dashboard says *the robot is busy* | Another button (or browser) is still running a command | Wait for it to finish |
| Dashboard shows `SerialException` / `no READY` | MKS not plugged in, or `main.py` run by hand at the same time | Check the USB cable and `python main.py ping`; do not use `main.py` while a button runs |
| Older laptop dashboard says *Raspberry Pi unavailable* | Pi solver not running or wrong address | `./run_solver.sh` on the Pi; set `PI_SERVER_URL` to the Pi's address |

## Command reference (on the Pi, in `~/rubiks-solver/pi`)

| Command | Does |
| --- | --- |
| `python main.py simulate` | Whole pipeline without hardware |
| `python main.py ping` | Check the MKS link |
| `python main.py move "R L' F2"` | Turn faces exactly as typed (no U) |
| `python main.py jog F 10` | Nudge one motor (microsteps, no overshoot) |
| `python main.py shake [--deg --cycles --faces]` | Wiggle the faces to settle the cube |
| `python main.py motors on` / `off` | Hold or release all motors |
| `python main.py show-faces [--image]` | Draw the face model on a photo to check it |
| `python main.py calibrate-rois` | Click sticker positions, `s` to skip (desktop; only with `sticker_source = "rois"`) |
| `python main.py calibrate-colours [--scrambled]` | Record face colours, one turn per key press |
| `python main.py solve --dry-run` | Scan + solve, print only (cube turned back) |
| `python main.py solve` | Scan, turn back, solve and execute |
| `python main.py read` | Scan only: print the cube and its 54-letter state (cube turned back) |
| `python main.py solve --state <54 letters>` | Solve and execute a known state, no scan |
| `python main.py --json <command>` | Also end with one `RESULT {...}` line (used by the dashboard) |
| `./run_ui.sh` | Start the robot dashboard on port 8080 (part 10) |
| `python web_app.py --fake-robot` | The dashboard with a pretend robot, no hardware |
| `python main.py bench [--speed --accel]` | Time the motors for speed tuning |
| `python -m pytest -q` | Run the unit tests |
