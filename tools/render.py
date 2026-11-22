"""MusicXML -> rehearsal MIDI (+ M4A): quiet piano, each choir part loud in its own instrument.

usage: uv run tools/render.py OUT_BASENAME score.mvt1.mxl [score.mvt2.mxl ...]
         [--tempo 104] [--focus S|A|T|B [--others]] [--sound instruments|aahs|oohs] [--no-piano | --accompaniment] [--no-audio]
         [--rit 42-43] [--tempo-at 44=84] [--dynamic 44=mp] [--fermata BAR[:BEAT]]

Files are concatenated in the order given (Audiveris splits a piece into movements
wherever the staff layout changes). Parts named "Piano", or written on two staves, are
accompaniment; every other part is a choir staff. A treble-clef choir staff is split into S (top note) / A (bottom note),
a bass-clef staff into T / B. A single note on a staff is given to both parts (unison), unless
another note on the staff is still held: then it belongs to the voice on the other side of that note.

Dynamics (pp..fff) and hairpins in the score scale loudness: the choir staves share one dynamic
level, the piano staves another. Fermatas and metronome marks in the score are played; OMR usually
misses those and any text ("rit.", "dim.", "Slower"), so add them by bar number:
  --rit FIRST-LAST      slow to 60% of the tempo across these bars, then resume
  --tempo-at BAR=BPM    new tempo from this bar (e.g. "Slower, with emphasis")
  --dynamic BAR=LEVEL   dynamic for every part from this bar (pp..fff; e.g. for a "dim." or a misread mark)
  --fermata BAR[:BEAT]  hold the note twice as long (BAR alone = the bar's last note)
"""
import argparse
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict

import mido
from music21 import clef, converter, dynamics, expressions, stream, tempo

TPQ = 480
SOUNDFONT = "~/Library/Audio/Sounds/Banks/GeneralUser-GS.sf2"
# name: (channel, GM program, pan 0-127)
TRACKS = {"Piano": (0, 0, 64), "S": (1, 73, 40), "A": (2, 71, 54), "T": (3, 60, 74), "B": (4, 42, 88)}
INSTRUMENT_NAMES = {"Piano": "Acoustic Grand Piano", "S": "Flute", "A": "Clarinet", "T": "French Horn", "B": "Cello"}
# --sound: what the choir parts are played on (GM programs); "instruments" keeps TRACKS' per-part instruments
VOICE_SOUNDS = {"aahs": (52, "Choir Aahs"), "oohs": (53, "Voice Oohs")}
# loudness multiplier per dynamic, relative to mf
LEVELS = {"ppp": 0.45, "pp": 0.55, "p": 0.7, "mp": 0.85, "mf": 1.0, "f": 1.12, "ff": 1.25, "fff": 1.35}
RIT_SLOWEST = 0.6  # --rit ends at this fraction of the tempo


def staff_is_bass(n):
    c = n.getContextByClass(clef.Clef)
    return c is not None and c.sign == "F"


class Dynamics:
    """Loudness over time for one group of staves: dynamic marks, with hairpins ramping between them."""

    def __init__(self):
        self.marks = []  # (offset, level)
        self.wedges = []  # (start, end, is_crescendo)

    def level(self, t):
        marks = sorted(self.marks)
        current = 1.0
        for at, lv in marks:
            if at <= t:
                current = lv
        for start, end, cresc in self.wedges:
            if start <= t < end:
                before = [lv for at, lv in marks if at <= start]
                after = [lv for at, lv in marks if at >= end]
                lo = before[-1] if before else 1.0
                hi = after[0] if after else lo * (1.15 if cresc else 1 / 1.15)
                return lo + (hi - lo) * (t - start) / (end - start)
        return current


def without_false_ties(path):
    """The MusicXML text with ties between notes of different pitch removed. OMR reads some slurs as ties,
    and music21 would merge the two notes into one, losing the second."""
    root = ET.parse(path).getroot()
    pitch = lambda n: tuple(n.findtext(f"pitch/{k}", "0") for k in ("step", "alter", "octave"))
    for part in root.findall("part"):
        last = {}                                   # (staff, voice) -> previous note
        for n in part.iter("note"):
            if n.find("chord") is not None or n.find("grace") is not None:
                continue
            key = (n.findtext("staff", "1"), n.findtext("voice", "1"))
            prev = last.get(key)
            if prev is not None and pitch(prev) != pitch(n):
                for el in [*prev.findall("tie"), *prev.findall("notations/tied")]:
                    if el.get("type") == "start":
                        (prev if el.tag == "tie" else prev.find("notations")).remove(el)
                for el in [*n.findall("tie"), *n.findall("notations/tied")]:
                    if el.get("type") == "stop":
                        (n if el.tag == "tie" else n.find("notations")).remove(el)
            last[key] = n
    return ET.tostring(root, encoding="unicode")


def collect(files, extra_dynamics=()):
    """Return ({track: [(start_ql, dur_ql, midi_pitch, level)]}, timing) for all files laid end to end.

    timing has "bars" {bar number: (start, length)}, "fermatas" [(start, dur)], "tempos" [(start, bpm)]."""
    events = defaultdict(list)
    timing = {"bars": {}, "fermatas": [], "tempos": []}
    base = 0.0
    for f in files:
        score = converter.parse(without_false_ties(f), format="musicxml").stripTies()
        for m in score.parts[0].getElementsByClass(stream.Measure):
            timing["bars"].setdefault(m.number, (base + float(m.offset), float(m.barDuration.quarterLength)))
        for mm in score.recurse().getElementsByClass(tempo.MetronomeMark):
            if mm.number:
                timing["tempos"].append((base + float(mm.getOffsetInHierarchy(score)), mm.getQuarterBPM()))
        groups = {"Piano": Dynamics(), "Choir": Dynamics()}
        for bar, value in extra_dynamics:
            m = score.parts[0].measure(bar)
            if m is not None:
                for dyn in groups.values():
                    dyn.marks.append((float(m.offset), LEVELS[value]))
        notes = []  # (group, is_piano, staff_is_bass, onset, dur, pitches, voice number)
        for part in score.parts:
            # the piano is the part named so, or the one written on two staves (decomposer names parts "Part N")
            is_piano = (part.partName or "").lower().startswith("piano") or isinstance(part, stream.PartStaff)
            dyn = groups["Piano" if is_piano else "Choir"]
            voice_of = {id(n): v.id for v in part.recurse().getElementsByClass(stream.Voice) for n in v.notes}
            flat = part.flatten()
            for d in flat.getElementsByClass(dynamics.Dynamic):
                if d.value in LEVELS:
                    dyn.marks.append((float(flat.elementOffset(d)), LEVELS[d.value]))
            for w in part.recurse().getElementsByClass(dynamics.DynamicWedge):
                ends = sorted(float(flat.elementOffset(n)) for n in (w.getFirst(), w.getLast()) if n in flat)
                if ends and ends[-1] > ends[0]:
                    dyn.wedges.append((ends[0], ends[-1], isinstance(w, dynamics.Crescendo)))
            for n in flat.notes:
                t, dur = float(flat.elementOffset(n)), float(n.quarterLength)
                if any(isinstance(e, expressions.Fermata) for e in n.expressions):
                    timing["fermatas"].append((base + t, dur))
                voice = str(voice_of.get(id(n), "1"))
                notes.append((dyn, is_piano, staff_is_bass(n), t, dur, [p.midi for p in n.pitches],
                              int(voice) if voice.isdigit() else 1))
        by_onset = defaultdict(list)  # (part's Dynamics, is_bass, onset) -> [(dur, pitch)]
        for dyn, is_piano, bass, t, dur, pitches, voice in notes:
            if is_piano:
                events["Piano"] += [(base + t, dur, p, dyn.level(t)) for p in pitches]
            else:
                by_onset[(id(dyn), bass, t)] += [(dur, p, dyn.level(t), voice) for p in pitches]
        for (dyn_id, bass, t), chord in by_onset.items():
            hi, lo = ("T", "B") if bass else ("S", "A")
            # by pitch; a unison goes to the parts by voice number (voice 1 is the upper part)
            chord.sort(key=lambda x: (x[1], -x[3]))
            tracks = (hi, lo)
            if len(chord) == 1:
                # a lone note under (over) a note still held on the staff is the lower (upper) voice
                # (a unison with the held note: by voice number, voice 1 being the upper part)
                p, voice = chord[0][1], chord[0][3]
                held = [(q, v) for (d2, b2, t2), c2 in by_onset.items() if d2 == dyn_id and b2 == bass and t2 < t
                        for dur, q, _, v in c2 if t2 + dur > t + 1e-6]
                if held and all(q > p or (q == p and v < voice) for q, v in held):
                    tracks = (lo,)
                elif held and all(q < p or (q == p and v > voice) for q, v in held):
                    tracks = (hi,)
            if hi in tracks:
                events[hi].append((base + t, *chord[-1][:3]))
            if lo in tracks:
                events[lo].append((base + t, *chord[0][:3]))
        base += float(score.highestTime)
    # identical notes from duplicated staves (e.g. the same part read twice) collapse here
    return {k: sorted(set(v)) for k, v in events.items()}, timing


def tempo_map(timing, events, bpm, rits, fermatas, tempo_at=()):
    """[(offset_ql, bpm)] from score tempo marks, --tempo-at, --rit bar ranges and fermatas (score + --fermata)."""
    bars = timing["bars"]
    marks = sorted(timing["tempos"]) or [(0.0, bpm)]
    if bpm is not None:  # --tempo overrides the score's first mark and scales the rest with it
        scale = bpm / marks[0][1]
        marks = [(t, b * scale) for t, b in marks]
    changes = {0.0: marks[0][1], **dict(marks), **{bars[bar][0]: b for bar, b in tempo_at}}

    def tempo_at(t):
        return [b for at, b in sorted(changes.items()) if at <= t][-1]

    for spec in rits:
        first, last = (int(x) for x in spec.split("-"))
        start = bars[first][0]
        end = bars[last][0] + bars[last][1]
        resume = tempo_at(end)
        from_bpm = tempo_at(start)
        beat = start
        while beat < end:  # step down each beat
            changes[beat] = from_bpm * (1 - (1 - RIT_SLOWEST) * (beat - start + 1) / (end - start))
            beat += 1.0
        changes.setdefault(end, resume)
    holds = list(timing["fermatas"])
    onsets = sorted({e[0] for evs in events.values() for e in evs})
    for spec in fermatas:
        bar, _, beat = spec.partition(":")
        start, length = bars[int(bar)]
        if beat:
            t = start + float(beat) - 1
        else:
            t = max(o for o in onsets if start <= o < start + length)
        later = [o for o in onsets if o > t]
        holds.append((t, (later[0] if later else start + length) - t))
    for t, dur in sorted(set(holds)):
        resume = tempo_at(t + dur)
        changes[t] = tempo_at(t) / 2
        changes.setdefault(t + dur, resume)
    return sorted(changes.items())


def velocity(track, focus, no_piano, others=False, accompaniment=False):
    if accompaniment:
        return 90 if track == "Piano" else 0
    if track == "Piano":
        return 0 if no_piano else 42
    if focus:
        # practice track: just the focused voice over the piano, unless --others asks for the rest quietly
        return 115 if track == focus else 45 if others else 0
    return 100


def write_midi(events, path, tempos, focus, no_piano, sound=None, others=False, accompaniment=False):
    mf = mido.MidiFile(ticks_per_beat=TPQ)
    meta = mido.MidiTrack()
    now = 0
    for t, bpm in tempos:
        tick = round(t * TPQ)
        meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=tick - now))
        now = tick
    mf.tracks.append(meta)
    for name, (ch, prog, pan) in TRACKS.items():
        vel = velocity(name, focus, no_piano, others, accompaniment)
        if name not in events or vel == 0:
            continue
        label = INSTRUMENT_NAMES[name]
        # --focus: the focused voice sings (oohs unless --sound says otherwise), the rest stay instruments
        voice_sound = (sound or "oohs") if name == focus else None if focus else sound
        if name != "Piano" and voice_sound in VOICE_SOUNDS:
            prog, label = VOICE_SOUNDS[voice_sound]
        msgs = []
        for start, dur, pitch, level in events[name]:
            on, off = round(start * TPQ), round((start + dur) * TPQ) - 10
            v = max(1, min(127, round(vel * level)))
            msgs.append((on, 1, mido.Message("note_on", channel=ch, note=pitch, velocity=v)))
            msgs.append((off, 0, mido.Message("note_off", channel=ch, note=pitch, velocity=0)))
        msgs.sort(key=lambda m: (m[0], m[1]))
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=f"{name} ({label})"))
        tr.append(mido.Message("program_change", channel=ch, program=prog))
        tr.append(mido.Message("control_change", channel=ch, control=10, value=pan))
        now = 0
        for t, _, m in msgs:
            tr.append(m.copy(time=t - now))
            now = t
        mf.tracks.append(tr)
    mf.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--tempo", type=float, help="quarter-note BPM (default: the score's mark, else 104)")
    ap.add_argument("--rit", action="append", default=[], metavar="FIRST-LAST", help="slow down over these bars")
    ap.add_argument("--fermata", action="append", default=[], metavar="BAR[:BEAT]", help="hold the note here")
    ap.add_argument("--tempo-at", action="append", default=[], metavar="BAR=BPM", help="change tempo at this bar")
    ap.add_argument("--dynamic", action="append", default=[], metavar="BAR=LEVEL",
                    help="set every part's dynamic at this bar (pp..fff)")
    ap.add_argument("--focus", choices=["S", "A", "T", "B"])
    ap.add_argument("--others", action="store_true", help="with --focus, keep the other voices (quiet instruments)")
    ap.add_argument("--no-piano", action="store_true")
    ap.add_argument("--accompaniment", action="store_true", help="piano only, at full volume (no voices)")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--sound", choices=["instruments", *VOICE_SOUNDS],
                    help="choir parts as separate instruments (default), or all as sung aahs/oohs;"
                         " with --focus only the focused voice (default oohs)")
    a = ap.parse_args()
    tempo_at = [(int(b), float(v)) for b, v in (x.split("=") for x in a.tempo_at)]
    extra_dyn = [(int(b), v) for b, v in (x.split("=") for x in a.dynamic)]
    if bad := [v for _, v in extra_dyn if v not in LEVELS]:
        ap.error(f"--dynamic level must be one of {', '.join(LEVELS)}, not {bad[0]}")
    events, timing = collect(a.files, extra_dyn)
    print({k: len(v) for k, v in events.items()})
    bpm = a.tempo if a.tempo or timing["tempos"] else 104
    write_midi(events, a.out + ".mid", tempo_map(timing, events, bpm, a.rit, a.fermata, tempo_at), a.focus, a.no_piano, a.sound, a.others, a.accompaniment)
    if not a.no_audio:
        sf = subprocess.run(["zsh", "-c", f"echo {SOUNDFONT}"], capture_output=True, text=True).stdout.strip()
        subprocess.run(["fluidsynth", "-ni", "-q", "-g", "0.8", "-F", a.out + ".wav", sf, a.out + ".mid"], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a.out + ".wav", "-c:a", "aac", "-b:a", "192k",
                        a.out + ".m4a"], check=True)
        subprocess.run(["rm", a.out + ".wav"], check=True)
    print("wrote", a.out + ".mid", "" if a.no_audio else a.out + ".m4a")


if __name__ == "__main__":
    main()
