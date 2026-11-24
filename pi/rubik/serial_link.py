"""USB serial link to the MKS Gen L firmware (see docs/protocol.md)."""
from __future__ import annotations

import time

MAX_TOKENS_PER_LINE = 64   # firmware limit; a whole solution fits in one line


class RobotError(RuntimeError):
    pass


class Robot:
    def __init__(self, port: str = "/dev/ttyUSB0", baud: int = 115200,
                 boot_timeout: float = 6.0, verbose: bool = False,
                 quarter_turns_only: bool = True):
        import serial  # pyserial
        self.verbose = verbose
        # True: every 180-degree turn is sent as two 90-degree turns.
        self.quarter_turns_only = quarter_turns_only
        self.settle_us = 2000      # pause the board adds after each move
        self.ser = serial.Serial(port, baud, timeout=0.2)
        # Opening the port resets the ATmega2560; wait for its banner.
        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            line = self._readline()
            if line.startswith("READY"):
                self.banner = line
                break
        else:
            raise RobotError("no READY from the board (wrong port or firmware?)")

    # -------------------------------------------------------------- basics
    def _readline(self) -> str:
        raw = self.ser.readline()
        line = raw.decode(errors="replace").strip()
        if line and self.verbose:
            print("  <", line)
        return line

    def command(self, cmd: str, timeout: float = 3.0) -> list[str]:
        """Send one line, return the info lines that came before OK."""
        if self.verbose:
            print("  >", cmd)
        self.ser.write((cmd + "\n").encode())
        info: list[str] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self._readline()
            if not line:
                continue
            if line.startswith("OK"):
                self.last_ok = line
                return info
            if line.startswith("ERR"):
                raise RobotError(f"{cmd!r}: {line}")
            info.append(line)
        raise RobotError(f"{cmd!r}: timed out after {timeout:.1f} s")

    # ------------------------------------------------------------- helpers
    def ping(self) -> bool:
        return self.command("PING") == ["PONG"]

    def enable(self, on: bool = True) -> None:
        self.command(f"EN {1 if on else 0}")

    def speed(self, max_sps: int, accel: int) -> None:
        self.command(f"SPEED {max_sps} {accel}")

    def settle(self, us: int) -> None:
        self.command(f"SETTLE {int(us)}")
        self.settle_us = int(us)

    def overshoot(self, deg: float) -> None:
        """Turn every MOV this many degrees past the target, then back (0 = off).
        Needs firmware 1.2 or newer."""
        try:
            self.command(f"OVERSHOOT {deg:g}")
        except RobotError as e:
            if "UNKNOWN" not in str(e):
                raise
            if deg > 0:
                raise RobotError("the board firmware has no OVERSHOOT command: "
                                 "upload firmware/cube_firmware (v1.2 or newer) to the MKS") from e
            # old firmware never overshoots, so 'off' needs nothing

    def shake(self, deg: float, cycles: int, faces: str = "") -> int:
        """Wiggle the faces back and forth by `deg` so the cube settles; they
        end exactly where they started. faces "" = all five. Returns the
        firmware-measured time in ms. Needs firmware 1.3 or newer."""
        cmd = f"SHAKE {deg:g} {int(cycles)}" + (f" {faces.upper()}" if faces else "")
        try:
            self.command(cmd, timeout=3.0 + 0.2 * cycles)
        except RobotError as e:
            if "UNKNOWN" in str(e):
                raise RobotError("the board firmware has no SHAKE command: "
                                 "upload firmware/cube_firmware (v1.3) to the MKS") from e
            raise
        parts = self.last_ok.split()
        return int(parts[1]) if len(parts) >= 2 else 0

    def current(self, ma: int) -> None:
        self.command(f"CUR {ma}")

    def drivers(self) -> list[str]:
        return self.command("DRV", timeout=5)

    def jog(self, face: str, steps: int) -> None:
        self.command(f"JOG {face} {steps}", timeout=10)

    def run(self, tokens: list[str], per_token_s: float = 1.0) -> int:
        """Execute robot tokens (e.g. ['R', "L'+R2", 'D2']).
        Returns the motion time in ms as measured by the firmware."""
        if self.quarter_turns_only:
            from .moves import expand_half_turns
            tokens = expand_half_turns(tokens)
        total_ms = 0
        for i in range(0, len(tokens), MAX_TOKENS_PER_LINE):
            chunk = tokens[i:i + MAX_TOKENS_PER_LINE]
            self.command("MOV " + " ".join(chunk),
                         timeout=2.0 + (per_token_s + self.settle_us / 1e6) * len(chunk))
            parts = self.last_ok.split()
            if len(parts) >= 3:
                total_ms += int(parts[2])
        return total_ms

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass
