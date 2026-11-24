# Pi ↔ MKS Gen L serial protocol

USB serial, 115200 baud, 8N1. One ASCII command per line (`\n`). Every
command ends with exactly one `OK …` or `ERR …` line; info lines may come
before it. Opening the port resets the board, which then prints
`READY rubik-fw 1.4`.

| Command | Example | Reply | Notes |
| --- | --- | --- | --- |
| `PING` | `PING` | `PONG` + `OK` | link check |
| `VER` | `VER` | `rubik-fw 1.4` + `OK` | |
| `MOV <tokens>` | `MOV R L' F2+B2 D` | `OK 4 312` | up to 64 tokens (a whole solution); all are checked before anything moves; the last number is the motion time in ms |
| `JOG <face> <steps>` | `JOG F -12` | `OK` | slow, signed microsteps; for lining up the adapters |
| `EN <0/1>` | `EN 0` | `OK` | 0 = motors off (cube can be turned by hand) |
| `SPEED <max> <accel>` | `SPEED 6000 150000` | `RAMP steps=… cruise_us=…` + `OK` | steps/s and steps/s²; rebuilds the ramp table |
| `SETTLE <us>` | `SETTLE 2000` | `OK` | pause after each move, 0–2000000 µs (up to 2 s) |
| `OVERSHOOT <deg>` | `OVERSHOOT 10` | `OVERSHOOT steps=44` + `OK` | every `MOV` turn goes this many degrees past its target in the direction of travel, then comes back; 0–30, decimals allowed, `0` = off (the default after a reset); `JOG` is never affected |
| `SHAKE <deg> <cycles> [faces]` | `SHAKE 3 5` or `SHAKE 3 5 FB` | `OK <ms>` | wiggles the faces together (+deg, then −deg/+deg `cycles` times, then back to 0) so the cube settles; ends exactly where it started; deg 0.5–15, cycles 1–50, faces default all five |
| `CUR <mA>` | `CUR 1200` | `OK` | run current, rms, 200–1600 mA, all drivers |
| `DRV` | `DRV` | one `DRV F ok …` line per driver + `OK` | UART check of each TMC2209 |

**Tokens.** `F R B L D` with no suffix (90° clockwise as seen from that face),
`'` (counter-clockwise) or `2` (180°). Two opposite faces joined by `+` turn
at the same time: `R+L'`, `F2+B2`, `D` has no partner on this robot.

**Errors.** `ERR BADMOVE <tok>`, `ERR NOMOTOR U`, `ERR NOTOPPOSITE <tok>`,
`ERR NOMOVES`, `ERR BADFACE <f>`, `ERR TOOMANY …`, `ERR UNKNOWN <cmd>`, `ERR USAGE …`, `ERR RANGE …`.
