"""What a command makes never stays in an installed skill's folder
(the brain wrote piano_lesson.md into
tutorial-builder). It is moved to ``<state>/workspace/made/<skill>``, where
the skill's page gallery reads it; files the install pinned and the skill's
caches stay where they are.
"""
from __future__ import annotations

import json
import os

from puffpai import brain_commands, skills, thing_media, things
from puffpai.finding import install


def _skill():
    folder = os.path.join(skills.skills_dir(create=True), "tutor")
    os.makedirs(os.path.join(folder, "scripts"), exist_ok=True)
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: tutor\ndescription: Builds tutorials.\n---\n# Tutor\n")
    with open(os.path.join(folder, "scripts", "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("pinned\n")
    with open(install._lock_path(), "w", encoding="utf-8") as fh:  # noqa: SLF001
        json.dump({"tutor": {"files": {"SKILL.md": "x", "scripts/notes.txt": "y"}}}, fh)
    return folder


def _run(command):
    return brain_commands.run({"command": command, "skill": "tutor"}, {"said_yes": True})


def test_a_file_made_in_a_skill_folder_is_moved_to_the_work_folder():
    folder = _skill()
    got = _run("printf 'C D E' > piano_lesson.md && mkdir -p out && printf x > out/keys.png"
               " && mkdir -p __pycache__ && printf c > __pycache__/c.pyc")
    assert got["worked"], got
    made = brain_commands.made_dir("tutor")
    assert not os.path.exists(os.path.join(folder, "piano_lesson.md"))
    assert not os.path.exists(os.path.join(folder, "out", "keys.png"))
    with open(os.path.join(made, "piano_lesson.md"), encoding="utf-8") as fh:
        assert fh.read() == "C D E"
    assert os.path.isfile(os.path.join(made, "out", "keys.png"))
    assert sorted(os.path.relpath(p, made).replace(os.sep, "/") for p in got["made"]) == \
        ["out/keys.png", "piano_lesson.md"]
    assert "work folder" in got["made_note"]
    # A cache the skill keeps for itself stays.
    assert os.path.isfile(os.path.join(folder, "__pycache__", "c.pyc"))
    # The install's own files stay.
    assert os.path.isfile(os.path.join(folder, "SKILL.md"))


def test_the_gallery_shows_what_was_moved():
    _skill()
    _run("mkdir -p out && printf x > out/keys.png")
    thing = things.get("skill-tutor")
    assert [r["name"] for r in thing_media.listing(thing)] == ["out/keys.png"]
    path, mime = thing_media.file_for(thing, "out/keys.png")
    assert mime == "image/png"
    assert os.path.realpath(path).startswith(os.path.realpath(brain_commands.made_dir("tutor")))
    assert thing_media.file_for(thing, "../../skills/tutor/SKILL.md") is None


def test_a_second_run_keeps_both_files():
    _skill()
    _run("printf one > lesson.md")
    _run("printf two > lesson.md")
    made = brain_commands.made_dir("tutor")
    assert sorted(os.listdir(made)) == ["lesson-2.md", "lesson.md"]


def test_a_pinned_file_is_not_moved_and_untouched_files_stay():
    folder = _skill()
    with open(os.path.join(folder, "old_output.md"), "w", encoding="utf-8") as fh:
        fh.write("from before")
    got = _run("printf changed >> scripts/notes.txt")
    assert got["worked"] and "made" not in got
    assert os.path.isfile(os.path.join(folder, "scripts", "notes.txt"))
    assert os.path.isfile(os.path.join(folder, "old_output.md"))


def test_a_command_in_the_work_folder_moves_nothing():
    got = brain_commands.run({"command": "printf x > here.md"}, {"said_yes": True})
    assert got["worked"] and "made" not in got
