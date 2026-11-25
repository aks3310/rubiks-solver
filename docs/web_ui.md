# Robot dashboard on the Pi

The dashboard runs on the Raspberry Pi and drives the robot. Open it from any
browser on the same network: `http://<pi-ip>:8080` (find the IP with
`hostname -I` on the Pi).

Every button runs `main.py`, so it behaves exactly like the command line
(same `config.toml`, speed, overshoot, scan plan and solver). **Every move is
shown on the 3D cube first, then sent to the robot.**

| Button | Runs | What you see |
| --- | --- | --- |
| 1 Scramble | `python main.py --json move "<moves>"` | random moves (F R B L D only) on screen; the 3D cube turns through them, then the robot does |
| 2 Read cube | `python main.py --json read` | the camera scan; the state read is shown on the 3D cube and in the facelets box |
| 3 Solve cube | `python main.py --json solve --dry-run --state <read state>`, then `python main.py --json move "<solution steps>"` | the solution is found, shown on the 3D cube, then turned by the robot; solution string, robot steps and times |
| Motors on / off | `python main.py --json motors on` / `off` | the indicator at the top right: *Motors: on* (green), *off* (red) or *unknown* (nothing run yet). Any move turns the motors on |

**Solve** uses the state from the last **Read** if the cube has not been
turned since. Otherwise `main.py solve` scans the cube first. A scan always
ends with the scan moves undone, so the cube is back in its scramble.

## Live camera

The **Live camera** panel shows what the robot's camera sees, with the face
outlines and sticker boxes drawn on (**Outlines: on/off**). It uses the same
field of view and the iso / shutter / white balance fixed in `config.toml`
`[camera]`, so it looks like the photos a scan takes.

- The camera is opened only while the page is showing the feed. About 5 s after
  the last viewer leaves (or presses **Pause feed**) it is closed again.
- While **Read cube** or **Solve cube** takes photos, the feed shows *Camera in
  use* and hands the camera to `main.py`; it comes back afterwards. Moves
  (Scramble, Live clicks, the solution) do not need the camera, so you can
  watch the robot turn the cube.
- To run `main.py read`, `solve`, `calibrate-colours` or `exposure_sweep.py` by
  hand while the dashboard is open, press **Pause feed** first (or close the
  page).
- `--no-camera` starts the dashboard without the feed.

## Clicking faces: two modes

Click a face of the 3D cube (not the top face, which has no motor) to turn it
90° clockwise. Drag to look around the cube.

- **Mode 1 · Live:** each click turns the face on the 3D cube, then the robot
  turns the same face (`main.py move "<face>"`). Fast clicks are queued and
  sent in order. Each one opens the USB link (about 2 s per click).
- **Mode 2 · Record** (the default): clicks turn the 3D cube only and build a
  move string. **Send to robot** sends the whole string as one
  `main.py move`. **Undo last** and **Clear** change the string (Clear puts the
  3D cube back where it was). Scramble, Read and Solve wait until the recorded
  moves are sent or cleared; switching to Live asks before discarding them.

Only one command runs at a time. While the dashboard is running, do not run
`main.py` robot commands by hand (both need the USB link and the camera).

## Start it

Copy the whole `pi/` folder to the Pi (it already contains `main.py` and
`rubik/`), with the venv set up as in SETUP.md. Then on the Pi:

```bash
cd ~/rubiks-solver/pi
./run_ui.sh
```

To start it automatically when the Pi boots, see the comments at the top of
`pi/rubik-ui.service`, then:

```bash
sudo cp rubik-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rubik-ui
```

## Try it without the robot

```bash
python web_app.py --fake-robot
```

This uses a pretend robot (a cube in memory, starting solved) so the page can
be tried on any computer. Open `http://localhost:8080`.

## Machine-readable main.py

`--json` (before the command) makes `move`, `read` and `solve` end with one
line `RESULT {...}`:

```
RESULT {"ok": true, "facelets": "...", "solver_moves": [...], "tokens": [...], "motion_ms": 1234, ...}
RESULT {"ok": false, "error": "U has no motor on this robot; use F, R, B, L or D"}
```
