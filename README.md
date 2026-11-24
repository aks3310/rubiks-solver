# Rubik's cube solver robot (TA6)

This repository combines two projects for the same five-motor robot:

| Folder | What it is | From |
| --- | --- | --- |
| `pi/` | Pi 5 program: camera scan, colour detection, Kociemba solve with U-free rewrite, USB link to the MKS board | rubiks-solver |
| `firmware/` | MKS Gen L v1.0 firmware (`cube_firmware`) and standalone test sketches | rubiks-solver |
| `docs/` | Wiring and serial protocol | rubiks-solver |
| `laptop_server/` | Laptop dashboard (3D cube, scrambles) that forwards to the Pi API | mepk1001 |
| `raspberry_pi/` | Pi HTTP solver API (port 8011), camera and executor scaffolds, vendored pure-Python Kociemba | mepk1001 |
| `server/` | Earlier laptop-local solver, kept for reference | mepk1001 |
| `index.html` | Static copy of the dashboard page | mepk1001 |

The two parts were written separately. **Settled:** the robot has five face
motors (F, R, B, L, D) and no regrip mechanism; U turns are rewritten into the
other five faces, and `raspberry_pi/robot_executor.py` now uses that logic from
`pi/`. **Still open:** camera view (`raspberry_pi/camera_scan.py` assumes U, R, F;
the real camera sees D, B, R) and solver library (bundled pure-Python Kociemba
in `raspberry_pi/` vs the compiled pip package in `pi/`).

## Part 1: Robot software (rubiks-solver)

**Step-by-step setup: [docs/SETUP.md](docs/SETUP.md).**
**Robot dashboard on the Pi (scramble, read, solve from a browser): [docs/web_ui.md](docs/web_ui.md).**

### Rubik's cube solver robot

Raspberry Pi 5 + Camera Module 3 Wide reads the cube, solves it with
Kociemba's two-phase algorithm and sends the moves over USB to an
MKS Gen L v1.0, which turns five faces (F R B L D) with NEMA 17 motors on
TMC2209 drivers. The top face has no motor: the software rewrites U turns
and uses the motors to bring hidden stickers into the camera's view.

```
rubiks-solver/
├── pi/                      host program (Python 3.11)
│   ├── main.py              command-line entry point
│   ├── config.toml          ports, speeds, camera, scan settings
│   ├── rubik/
│   │   ├── cube.py          54-facelet model, moves, 24 orientations
│   │   ├── moves.py         simplify, U-free rewrite, parallel R+L turns
│   │   ├── scan_planner.py  which moves/photos reveal all stickers
│   │   ├── vision.py        camera, sticker sampling, colour assignment
│   │   ├── solver.py        Kociemba + best-orientation search
│   │   └── serial_link.py   USB link to the firmware
│   └── tests/test_core.py   unit tests (no hardware needed)
├── firmware/cube_firmware/  Arduino sketch for the MKS Gen L
└── docs/                    wiring.md, protocol.md
```

#### 1. Firmware (MKS Gen L)

1. Arduino IDE → Library Manager → install **TMCStepper** (teemuatlut).
2. Open `firmware/cube_firmware/cube_firmware.ino`.
3. Board **Arduino Mega or Mega 2560**, processor **ATmega2560**, the board's COM port.
4. Upload. In Serial Monitor (115200, newline) you should see `READY rubik-fw 1.0`.
5. Wire the UART as in `docs/wiring.md`, then send `DRV`: every driver must say `ok`.
6. Send `MOV F`, `MOV R`, … and check each face turns **clockwise as seen
   from that face**. Fix any that don't with the `*_INVERT` flags in `config.h`.

#### 2. Raspberry Pi 5

```bash
sudo apt install -y python3-picamera2 python3-opencv python3-numpy python3-scipy python3-dev libffi-dev build-essential
cd rubiks-solver/pi
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q          # model and planner tests
python main.py simulate      # whole pipeline without hardware
```

The first `kociemba` call builds its tables (a few seconds); later calls are fast.

#### 3. One-time calibration

1. Load the cube **white on top, green at the front** and keep this for every run.
2. `python main.py calibrate-rois` (needs the Pi desktop or VNC): click the
   centre of each sticker it asks for; press `s` for any sticker the camera
   cannot see. Then copy the printed `visible = [...]` list into `config.toml`.
3. With a **solved** cube loaded: `python main.py calibrate-colours`.
   It scans, stores one reference colour per face, and turns the cube back.

#### 4. Solve

```bash
python main.py solve --dry-run   # scan + solve, print, no solution moves
python main.py solve             # prints software time and firmware-measured motion time
```

Photos of every run are kept in `pi/logs/<time>/` for debugging.

#### Notes

* Speed: the goal is a solve in under 2 s after the scan. Firmware runs 1/8
  microstepping (400 steps per quarter turn) with a pre-computed ramp. Tune with
  `python main.py bench --speed 8000 --accel 250000` (a random sequence and its
  inverse; the cube must end where it started). Raise until turns skip, back off
  ~15 %, then put the values in `config.toml → [motion]`. Start slower
  (`--speed 3000 --accel 60000`) until the adapters and alignment are proven.
* Motor current is set in firmware (`RUN_CURRENT_MA`, default 1350 mA rms,
  80 % of the 1.68 A JK42HS48-1684 rating); `python main.py` has no current command,
  use `CUR <mA>` in a serial terminal to try other values.

## Part 2: TA6 dashboard and Pi API (mepk1001)

### TA6 Cube Solver

TA6 is a five-motor Rubik's Cube robot. The Raspberry Pi runs the Kociemba two-phase solver and robot API. A laptop runs the dashboard and forwards requests to the Pi. Kociemba finds short practical solutions but does not guarantee the mathematically shortest solution for every state.

#### Current status

- The dashboard has a 3D cube, mouse turns, random/custom scrambles, and multiple candidate routes ranked by an estimated time or move count.
- The Pi can solve a supplied 54-sticker facelet string in URFDLB order and reject impossible states.
- The first laptop server to contact the Pi claims it while the Pi solver process is running. Restarting the Pi solver releases that claim.
- Camera recognition is a calibration scaffold only. Motor control goes through firmware/cube_firmware; robot_executor.py is a dry run unless --execute is given. There is no top-face motor; U turns are rewritten into turns of the other five faces (no regrip).

#### Requirements

- Current Raspberry Pi OS with Python 3. The Pi solver uses the standard library and bundled solver; no pip install is needed on the Pi.
- A Windows laptop with Git and Python 3.10 or newer.
- Pi and laptop on the same reachable local network. Enable SSH on the Pi for the copy instructions below.
- Allow the laptop dashboard through Windows Firewall on a Private network if prompted. Laptop dashboard port is 8010; Pi API port is 8011.

#### Set up a new laptop

Open PowerShell and clone TA6:

```powershell
git clone https://github.com/nakul-garg/mepk1001.git TA6
cd TA6
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r laptop_server\requirements.txt
```

If PowerShell blocks environment activation, run the setup commands in a new PowerShell window or use .venv\Scripts\python.exe explicitly in the server command below.

#### Copy and start the Raspberry Pi solver

Copy the whole raspberry_pi folder from the TA6 repository. Do not omit vendor/pure_python or its .pkl pruning tables. `robot_executor.py` also needs the `pi` folder next to it, so copy that too (`scp -r .\pi pi@raspberrypi.local:~/`). From the TA6 root on Windows PowerShell:

```powershell
scp -r .\raspberry_pi pi@raspberrypi.local:~/
```

Replace raspberrypi.local with the Pi's IP address if name lookup does not work. On the Pi terminal (or over SSH):

```bash
cd ~/raspberry_pi
bash setup.sh
./run_solver.sh
```

Leave this process running. It listens on port 8011. Find the Pi address with hostname -I. No Pi packages or robot ID are required. To release the first-laptop lock, stop the process with Ctrl+C and start it again with ./run_solver.sh.

#### Start the laptop dashboard

Replace the example address with the Pi address. In PowerShell at the TA6 repository root:

```powershell
$env:PI_SERVER_URL = "http://192.168.1.50:8011"
Set-Location .\laptop_server
..\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8010
```

Alternatively, with the virtual environment active, enter laptop_server and run:

```powershell
.\start_server.ps1 -PiUrl http://192.168.1.50:8011
```

The first laptop server to reach the Pi claims it. Other laptop servers are rejected until the Pi solver restarts. Open http://localhost:8010/ on the laptop. If the Pi address changes, stop and restart the laptop server with its new address.

#### Camera scan variables and pixel calibration

The scaffold is raspberry_pi/camera_scan.py. It intentionally contains no guessed pixel coordinates or color calibration. When camera placement is finalized, fill SAMPLE_PIXEL_COORDINATES with named sticker-center locations and COLOR_REFERENCE_RGB with measured color references. Raw samples are retained by sticker name in scan_rgb_variables (for example F5); classified letters are retained in facelet_color_variables. The pixel reader is passed in, so the camera library can be selected once you share the camera model.

A fixed diagonal camera view sees only three faces at once. To read all 54 stickers, `pi/rubik/scan_planner.py` turns faces between photos (2 photos for a corner view); no regrip is needed. Exact pixels and colour calibration are still needed before automatic scanning can run.

#### Project folders

- laptop_server: dashboard, Pi proxy, and Windows launcher.
- raspberry_pi/solver_app.py: standard-library API and Pi-local solver.
- raspberry_pi/camera_scan.py: named pixel and color variables awaiting calibration.
- raspberry_pi/robot_executor.py: five-motor contract and dry-run scaffold.
- server: earlier laptop-local solver retained for reference.

#### Hardware integration still needed

The robot has five face motors on the MKS Gen L v1.0: F, R, B, L, D on sockets Z, E1, E0, Y, X (set in firmware/cube_firmware/config.h; see docs/wiring.md). There is no top motor and no regrip mechanism. `raspberry_pi/robot_executor.py` rewrites U turns with `pi/rubik/moves.py` and sends the steps to `firmware/cube_firmware` over USB; it is a dry run unless `--execute` is given. Confirm each face turns clockwise with `MOV F`, `MOV R`, and so on before running full solves.
