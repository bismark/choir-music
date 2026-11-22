"""Local editor for fixing wrong notes in an OMR MusicXML file.

Usage:
  python tools/edit.py "scans/<piece>/<piece>.musicxml" [--port 8765]

Opens a browser page showing 1-3 bars at a time, each voice in its own colour.
Press E for edit mode, drag a note up or down (or select it and use the arrow
keys), then Cmd-S to save back to the same file. The first save keeps the
untouched file as <file>.orig. A PDF in the same folder is shown as the scan.
"""
import argparse
import json
import re
import shutil
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PAGE = Path(__file__).with_name("editor.html")
HEADER = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
          b'<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN"'
          b' "http://www.musicxml.org/dtds/partwise.dtd">\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score", type=Path)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    score = args.score.resolve()
    pdfs = sorted(score.parent.glob("*.pdf"))
    scan = pdfs[0] if pdfs else None

    class Handler(BaseHTTPRequestHandler):
        def reply(self, body, ctype, code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.reply(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/score.musicxml":
                self.reply(score.read_bytes(), "application/xml")
            elif path == "/scan.pdf" and scan:
                self.reply(scan.read_bytes(), "application/pdf")
            elif path == "/meta":
                self.reply(json.dumps({"title": score.stem, "scan": bool(scan)}).encode(), "application/json")
            else:
                self.reply(b"not found", "text/plain", 404)

        def do_PUT(self):
            if urlparse(self.path).path != "/score.musicxml":
                return self.reply(b"not found", "text/plain", 404)
            body = self.rfile.read(int(self.headers["Content-Length"]))
            try:
                ET.fromstring(body)
            except ET.ParseError as e:
                return self.reply(f"not saved, invalid XML: {e}".encode(), "text/plain", 400)
            # Match decomposer's formatting so diffs against .orig show only the edits.
            body = re.sub(rb"^(<\?xml[^>]*\?>)?\s*(<!DOCTYPE[^>]*>)?\s*", b"", body)
            body = HEADER + re.sub(rb'(?<=[^ ])/>', b" />", body).rstrip() + b"\n"
            backup = score.with_name(score.name + ".orig")
            if not backup.exists():
                shutil.copy2(score, backup)
            tmp = score.with_name(score.name + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(score)
            self.reply(b"saved", "text/plain")

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Editing {score.name} at {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
