# Choir rehearsal tracks

Score scan (PDF) → MusicXML → fix wrong notes → practice audio (full mix + one track per voice).

All commands run from this folder (`~/Programming/personal/choir-music`).

## 1. Add a piece

Make a folder under `scans/` and put the scan PDF in it:

```
scans/<piece>/score.pdf
```

## 2. Read the scan into notes (decomposer)

```
~/Applications/decomposer/bin/decomposer convert scans/<piece> --title "<Title>"
```

This writes `scans/<piece>/<Title>.musicxml`. Run it yourself in a terminal (or `! ...` in Claude Code).

Other decomposer commands:

```
~/Applications/decomposer/bin/decomposer inspect scans/<piece>                     # page order + contact sheet
~/Applications/decomposer/bin/decomposer render  "scans/<piece>/<Title>.musicxml"  # engrave it to compare with the scan
```

Our copy of decomposer is locally patched (`patches/copisteria.patch` in the decomposer folder) so it
doesn't drop bars. If you reinstall it, re-apply that patch.

## 3. Check nothing was dropped

```
uv run tools/bars.py "scans/<piece>/<Title>.musicxml"
```

Prints the bar and staff count. Compare the bar count with the last bar number on the scan.

### Key changes

The scan reader misses a key change printed mid-line with naturals (e.g. A♭ → C) and only picks it up at
the next page or system. Check every key change against the scan, then fix any that are wrong:

```
uv run tools/key.py "scans/<piece>/<Title>.musicxml" 20=0 72=0   # BAR=FIFTHS: 0 = C, -4 = 4 flats, 4 = 4 sharps
```

It moves the change to that bar and respells the notes in between.

### Pickup bar

The scan reader writes a pickup (a short first bar) as a full bar padded with rests, so the track would start
with a bar of silence. Make bar 1 a pickup as long as its notes (or pass `--beats N`):

```
uv run tools/pickup.py "scans/<piece>/<Title>.musicxml"
```

## 4. Fix wrong notes (editor)

```
python3 tools/edit.py "scans/<piece>/<Title>.musicxml"
```

Opens a browser page: 1–3 bars on the left, the scan page on the right. Ctrl-C in the terminal to quit.

| Key | Does |
| --- | --- |
| `←` `→` (`⇧` for a page of bars) | move through bars; or type a bar number in the box |
| `1` `2` `3` | bars shown |
| click a note | hear it |
| `Space` | play / stop |
| `F` | follow: keep playing and turn the bars |
| `E` | edit mode |
| drag a note, or `↑` `↓` | move it up/down within the key (edit mode) |
| `⇧↑` `⇧↓` | move it a semitone (edit mode) |
| click an orange `+` box | fill a gap where the scan reader dropped a note (edit mode), then drag to the pitch |
| `Tab` | next note |
| `⌘Z` / `⌘S` | undo / save |

The first save keeps the untouched file as `<Title>.musicxml.orig`. To see what you changed:

```
diff "scans/<piece>/<Title>.musicxml.orig" "scans/<piece>/<Title>.musicxml"
```

Colours: soprano red, alto amber, tenor green, bass blue, piano grey. On each choir staff the top note is
S/T and the bottom note A/B, the same split the audio uses.

The editor can't delete notes, change rhythms, or add a note where there's no gap.

## 5. Make the audio

Full mix:

```
uv run tools/render.py "out/<Title> - full mix" "scans/<piece>/<Title>.musicxml"
```

One practice track per voice (just that voice, sung on "oohs", over quiet piano):

```
for v in S A T B; do
  uv run tools/render.py "out/<Title> - $v practice" "scans/<piece>/<Title>.musicxml" --focus $v
done
```

Each run writes a `.mid` and an `.m4a` to `out/`. Re-run after every editor save.

### render.py options

| Option | Does |
| --- | --- |
| `--focus S\|A\|T\|B` | practice track: that voice on oohs over quiet piano; other voices left out |
| `--sound aahs` / `--sound oohs` / `--sound instruments` | choir sound: for every voice, or with `--focus` only the focused one |
| `--tempo 96` | quarter-note BPM (default: the score's mark, else 104) |
| `--rit 42-43` | slow down over bars 42–43, then resume |
| `--tempo-at 44=84` | new tempo from bar 44 (e.g. "Slower, with emphasis") |
| `--dynamic 44=mp` | dynamic from bar 44 (pp … fff), for marks the scan reader missed |
| `--fermata 51` / `--fermata 24:3` | hold the last note of bar 51 / the note on beat 3 of bar 24 |
| `--others` | with `--focus`: keep the other voices, quietly (by default a practice track is just that voice + piano) |
| `--no-piano` | voices only |
| `--accompaniment` | piano only, at full volume (no voices) |
| `--no-audio` | MIDI only, skip the .m4a |

Dynamics, hairpins and fermatas printed in the score are picked up automatically; the flags are only for
text marks the scan reader misses (rit., dim., "Slower").

Only add a `--fermata` where the scan actually has one. Options repeat, e.g. what "Come Ye Thankful People Come" uses:

```
uv run tools/render.py "out/Come Ye Thankful People Come - full mix" \
  "scans/come-ye-thankful/Come Ye Thankful People Come.musicxml" \
  --rit 4-4 --rit 20-20 --rit 25-25 --rit 42-43 --tempo-at 44=84 --dynamic 44=mp --rit 49-50
```

## Setup (already done on this Mac)

- `brew install fluidsynth ffmpeg`
- Soundfont: `~/Library/Audio/Sounds/Banks/GeneralUser-GS.sf2`
- Python deps: `uv sync` (from `pyproject.toml`; `uv run` also does this automatically)
- decomposer: `~/Applications/decomposer` (`bin/decomposer setup` once after installing, then re-apply `patches/copisteria.patch`)
