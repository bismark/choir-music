"""Fix a key change the scan reader missed or put in the wrong bar.

Usage:
  uv run tools/key.py "scans/<piece>/<Title>.musicxml" 20=0 72=0

Each BAR=FIFTHS sets the key from that bar on (FIFTHS: 0 = C, -4 = four flats, 4 = four sharps).
It lasts until the score's next key change that differs from it. A later key change in the file that
merely repeats the new key (the reader often catches it at the next system) is removed.

Notes in bars whose key changed are respelled to the new key, unless they carry their own printed
accidental, follow one earlier in the bar, or are tied over from a note of the old key.
The first run keeps the untouched file as <file>.orig, the same as the editor.
"""
import argparse
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

HEADER = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
          b'<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN"'
          b' "http://www.musicxml.org/dtds/partwise.dtd">\n')
SHARPS, FLATS = "FCGDAEB", "BEADGCF"


def key_alter(fifths: int, step: str) -> int:
    if fifths > 0 and step in SHARPS[:fifths]:
        return 1
    if fifths < 0 and step in FLATS[:-fifths]:
        return -1
    return 0


def measure_key(m):
    k = m.find("attributes/key/fifths")
    return int(k.text) if k is not None else None


def set_key(m, fifths):
    attrs = m.find("attributes")
    if attrs is None:
        attrs = ET.Element("attributes")
        i = 0
        while i < len(m) and m[i].tag in ("print", "barline"):
            i += 1
        m.insert(i, attrs)
    for k in attrs.findall("key"):
        attrs.remove(k)
    key = ET.Element("key")
    ET.SubElement(key, "fifths").text = str(fifths)
    attrs.insert(1 if attrs.find("divisions") is not None else 0, key)


def drop_key(m):
    attrs = m.find("attributes")
    for k in attrs.findall("key"):
        attrs.remove(k)
    if not len(attrs):
        m.remove(attrs)


def fix_part(part, changes):
    measures = part.findall("measure")
    old, new, k_old, k_new = [], [], 0, 0
    for m in measures:
        bar, file_key = int(m.get("number")), measure_key(m)
        if file_key is not None:
            k_old = file_key
        if bar in changes:
            k_new = changes[bar]
        elif file_key is not None:
            k_new = file_key
        old.append(k_old)
        new.append(k_new)

    for i, m in enumerate(measures):
        bar = int(m.get("number"))
        if bar in changes:
            set_key(m, changes[bar])
        elif measure_key(m) is not None and i and new[i] == new[i - 1]:
            drop_key(m)                             # repeats the key already in force

    respelled = 0
    last = {}                                       # (step, octave, staff) -> alter of the last such note
    for i, m in enumerate(measures):
        carried = set()                             # accidentals printed earlier in this bar
        for n in m.findall("note"):
            p = n.find("pitch")
            if p is None:
                continue
            step, octave = p.findtext("step"), p.findtext("octave")
            where = (step, octave, n.findtext("staff", "1"))
            alter_el = p.find("alter")
            alter = int(float(alter_el.text)) if alter_el is not None else 0
            tied_in = any(t.get("type") == "stop" for t in n.findall("tie"))
            if n.find("accidental") is not None:
                carried.add(where)
            elif old[i] != new[i] and where not in carried:
                want = last.get(where, alter) if tied_in else key_alter(new[i], step)
                if want != alter:
                    respelled += 1
                    if alter_el is None:
                        alter_el = ET.Element("alter")
                        p.insert(1, alter_el)
                    if want:
                        alter_el.text = str(want)
                    else:
                        p.remove(alter_el)
                    alter = want
            last[where] = alter
    return respelled


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score", type=Path)
    ap.add_argument("changes", nargs="+", help="BAR=FIFTHS, e.g. 20=0")
    args = ap.parse_args()
    changes = {int(b): int(f) for b, f in (c.split("=") for c in args.changes)}

    tree = ET.parse(args.score)
    total = 0
    for part in tree.getroot().findall("part"):
        total += fix_part(part, changes)

    orig = args.score.with_name(args.score.name + ".orig")
    if not orig.exists():
        shutil.copy(args.score, orig)
    args.score.write_bytes(HEADER + ET.tostring(tree.getroot(), encoding="utf-8"))
    print(f"key set at bar(s) {', '.join(map(str, sorted(changes)))}; {total} notes respelled")


if __name__ == "__main__":
    main()
