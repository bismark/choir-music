"""Make the first bar a pickup (anacrusis) when the scan reader wrote it as a full bar of rests and a few notes.

Usage:
  uv run tools/pickup.py "scans/<piece>/<Title>.musicxml"            # pickup as long as the notes in bar 1
  uv run tools/pickup.py "scans/<piece>/<Title>.musicxml" --beats 1  # or say how long it is

Bar 1 is marked as a pickup (implicit="yes") in every part, and its rests are shortened to the pickup's length,
so the rendered track starts on the pickup instead of after a bar's silence and the editor draws it short.
The first run keeps the untouched file as <file>.orig, the same as the editor.
"""
import argparse
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

HEADER = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
          b'<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN"'
          b' "http://www.musicxml.org/dtds/partwise.dtd">\n')
TYPES = [(4, "whole"), (2, "half"), (1, "quarter"), (0.5, "eighth"), (0.25, "16th"), (0.125, "32nd")]


def note_type(quarters):
    """The note type (and whether dotted) of a length in quarter notes, or None when it is not a single note."""
    for q, name in TYPES:
        if abs(quarters - q) < 1e-9:
            return name, False
        if abs(quarters - 1.5 * q) < 1e-9:
            return name, True
    return None


def divisions(part):
    d = part.find("measure/attributes/divisions")
    return int(d.text) if d is not None else 1


def notes_end(m):
    """Where the last sounding note of the bar ends, in divisions."""
    cur = end = 0
    for e in m:
        dur = int(e.findtext("duration", "0"))
        if e.tag == "backup":
            cur -= dur
        elif e.tag == "forward":
            cur += dur
        elif e.tag == "note" and e.find("grace") is None:
            if e.find("chord") is None:
                start, cur = cur, cur + dur
            if e.find("rest") is None:
                end = max(end, start + dur)
    return end


def shorten(m, length, div):
    """Cut rests (and spacers) in the bar so that no voice runs past ``length`` divisions."""
    cur = 0
    for e in list(m):
        dur_el = e.find("duration")
        dur = int(dur_el.text) if dur_el is not None else 0
        if e.tag == "backup":
            cur -= dur
        elif e.tag in ("forward", "note") and e.find("chord") is None and e.find("grace") is None:
            if cur + dur > length and (e.tag == "forward" or e.find("rest") is not None):
                dur = max(length - cur, 0)
                if not dur:
                    m.remove(e)
                    continue
                dur_el.text = str(dur)
                if e.tag == "note":
                    # a plain rest of the right type: readers stretch a whole-bar rest to the time signature
                    e.find("rest").attrib.pop("measure", None)
                    for t in e.findall("type") + e.findall("dot"):
                        e.remove(t)
                    kind = note_type(dur / div)
                    if kind:
                        at = list(e).index(e.find("voice") if e.find("voice") is not None else dur_el) + 1
                        e.insert(at, ET.Element("type"))
                        e[at].text = kind[0]
                        if kind[1]:
                            e.insert(at + 1, ET.Element("dot"))
            cur += dur


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score", type=Path)
    ap.add_argument("--beats", type=float, help="the pickup's length in quarter notes (default: its notes')")
    args = ap.parse_args()

    tree = ET.parse(args.score)
    parts = tree.getroot().findall("part")
    quarters = args.beats or max(notes_end(p.find("measure")) / divisions(p) for p in parts)
    if not quarters:
        raise SystemExit("bar 1 has no notes; pass --beats")
    for p in parts:
        m = p.find("measure")
        m.set("implicit", "yes")
        shorten(m, round(quarters * divisions(p)), divisions(p))

    orig = args.score.with_name(args.score.name + ".orig")
    if not orig.exists():
        shutil.copy(args.score, orig)
    args.score.write_bytes(HEADER + ET.tostring(tree.getroot(), encoding="utf-8"))
    print(f"bar 1 is now a pickup of {quarters:g} beat(s)")


if __name__ == "__main__":
    main()
