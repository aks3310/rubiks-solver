"""[cube] top / front: which colour is on which face as the cube is loaded."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rubik.cube import FACES, STANDARD_SCHEME, face_colours, net  # noqa: E402
from rubik.vision import load_references, save_references  # noqa: E402

OPPOSITE = {"U": "D", "D": "U", "R": "L", "L": "R", "F": "B", "B": "F"}
PAIRS = [{"white", "yellow"}, {"green", "blue"}, {"red", "orange"}]


def test_default_is_white_up_green_front():
    assert face_colours() == STANDARD_SCHEME


@pytest.mark.parametrize("top,front,right", [
    ("yellow", "green", "orange"),     # turned upside down about the front-back axis
    ("white", "red", "blue"),          # turned a quarter about the vertical axis
    ("yellow", "blue", "red"),
    ("green", "white", "orange"),
])
def test_known_orientations(top, front, right):
    fc = face_colours(top, front)
    assert (fc["U"], fc["F"], fc["R"]) == (top, front, right)


def test_all_24_orientations_are_real_cubes():
    colours = sorted(STANDARD_SCHEME.values())
    found = set()
    for top in colours:
        for front in colours:
            if top == front or {top, front} in PAIRS:
                with pytest.raises(ValueError):
                    face_colours(top, front)
                continue
            fc = face_colours(top.upper(), f" {front} ")       # case / spaces ignored
            assert sorted(fc.values()) == colours
            assert all({fc[f], fc[OPPOSITE[f]]} in PAIRS for f in FACES)
            found.add((fc["U"], fc["F"]))
    assert len(found) == 24


def test_unknown_colour_is_refused():
    with pytest.raises(ValueError, match="pink"):
        face_colours("pink", "green")


def test_references_are_remapped_when_orientation_changes(tmp_path):
    # calibrated white up / green front: each face's reference is "its colour"
    std = face_colours("white", "green")
    refs = {f: [float(i), 0.0, 0.0] for i, f in enumerate(FACES)}
    path = tmp_path / "colours.json"
    save_references(path, refs, std)
    saved = json.loads(path.read_text())
    assert saved["_face_colours"] == std
    same, note = load_references(path, std)
    assert same == refs and note is None
    # now loaded yellow up / green front: U must get the old D (yellow) reference
    new = face_colours("yellow", "green")
    moved, note = load_references(path, new)
    by_colour = {std[f]: refs[f] for f in FACES}
    assert moved == {f: by_colour[new[f]] for f in FACES}
    assert moved["U"] == refs["D"] and moved["R"] == refs["L"] and "yellow up" in note


def test_old_colour_files_count_as_white_up_green_front(tmp_path):
    path = tmp_path / "colours.json"
    path.write_text(json.dumps({f: [1.0, 2.0, 3.0] for f in FACES}))
    refs, note = load_references(path, face_colours())
    assert note is None and set(refs) == set(FACES)


def test_net_uses_the_loaded_colours():
    state = "".join(f * 9 for f in FACES)
    text = net(state, colours=True, face_colour=face_colours("yellow", "green"))
    assert text.splitlines()[1].split() == ["Y", "Y", "Y"]     # top row is yellow now
    assert "held yellow up, green front" in text
