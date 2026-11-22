"""Print how many bars and parts each MusicXML file has, so dropped systems are obvious."""
import sys
import warnings

warnings.filterwarnings("ignore")
from music21 import converter  # noqa: E402

total = 0
for f in sys.argv[1:]:
    parts = converter.parse(f).parts
    bars = len(parts[0].getElementsByClass("Measure")) if parts else 0
    total += bars
    print(f"{bars} bars, {len(parts)} staves  ({f.rsplit('/', 1)[-1]})")
