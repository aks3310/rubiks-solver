# Robot dashboard on the Pi

The dashboard runs on the Raspberry Pi and drives the robot. Open it from any
browser on the same network: `http://<pi-ip>:8080` (find the IP with
`hostname -I` on the Pi).

Every button runs `main.py`, so it behaves exactly like the command line
(same `config.toml`, speed, overshoot, scan plan and solver):

| Button | Runs | What you see |
| --- | --- | --- |
| 1 Scramble | `python main.py --json move "<moves>"` | random moves (F R B L D only), shown on screen, turned by the robot and on the 3D cube |
| 2 Read cube | `python main.py --json read` | the camera scan; the state read is shown on the 3D cube and in the facelets box |
| 3 Solve cube | `python main.py --json solve --state <read state>` | solution string, robot steps and times; the 3D cube turns to solved |

**Solve** uses the state from the last **Read** if the cube has not been
scrambled since. Otherwise `main.py solve` scans the cube first. A scan always
ends with the scan moves undone, so the cube is back in its scramble.

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
