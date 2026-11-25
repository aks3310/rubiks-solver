"""Write settings back into config.toml without disturbing the rest of it.

Only whole `key = value` lines inside one [section] are touched; comments,
other keys and other sections stay exactly as they were. Used by
`calibrate-colours` to store the camera settings it calibrated with.
"""
from __future__ import annotations

import re
from pathlib import Path


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, float):
        return f"{value:g}" if value == round(value, 6) else f"{value:.6g}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    return str(value)


def set_keys(text: str, section: str, values: dict, note: str = "") -> str:
    """Return config text with `key = value` set inside [section] for every
    item of values. An existing line for the key is replaced (an active one
    first, otherwise a commented-out example such as `# iso = 200`); a missing
    key is added at the end of the section. `note` is written as a comment."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    header = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")
    start = next((i for i, ln in enumerate(lines)
                  if (m := header.match(ln)) and m.group(1).strip() == section), None)
    if start is None:                       # no such section: add it at the end
        while lines and lines[-1].strip() == "":
            lines.pop()
        lines += ["", f"[{section}]"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if header.match(lines[i])), len(lines))
    comment = f"   # {note}" if note else ""
    for key, value in values.items():
        new = f"{key} = {_fmt(value)}{comment}"
        active = re.compile(rf"^\s*{re.escape(key)}\s*=")
        example = re.compile(rf"^\s*#\s*{re.escape(key)}\s*=")
        hit = next((i for i in range(start + 1, end) if active.match(lines[i])), None)
        if hit is None:
            hit = next((i for i in range(start + 1, end) if example.match(lines[i])), None)
        if hit is not None:
            lines[hit] = new
        else:                               # after the section's last non-blank line
            at = end
            while at - 1 > start and lines[at - 1].strip() == "":
                at -= 1
            lines.insert(at, new)
            end += 1
    out = nl.join(lines)
    return out if out.endswith(nl) else out + nl


def set_keys_in_file(path: str | Path, section: str, values: dict, note: str = "") -> None:
    """set_keys on a file, written via a temporary file so a crash cannot
    leave half a config.toml behind."""
    path = Path(path)
    text = path.read_bytes().decode("utf-8")
    new = set_keys(text, section, values, note)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(new.encode("utf-8"))
    tmp.replace(path)
