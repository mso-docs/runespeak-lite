#!/usr/bin/env python3
"""Opt-in real-model desktop test. Briefly opens a window and plays test audio."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import wave
from unittest.mock import patch

from runespeak.app import App


def pump(root, condition, timeout=90):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        root.update()
        time.sleep(.02)
    assert condition(), 'Timed out waiting for desktop operation'


def main():
    errors = []
    with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, {'RUNESPEAK_RUNTIME_DIR': runtime}), patch('runespeak.app.messagebox.showerror', side_effect=lambda *args: errors.append(args)):
        root = tk.Tk(className='RunespeakLite')
        root.withdraw()
        app = App(root)
        try:
            text = 'This is the streaming narration test. Each section is read while the next one is generated. ' * 3
            payload = json.dumps({'action': 'read', 'text': text, 'source': 'https://example.com/selected'}).encode()
            # Use the real Firefox native-message framing and companion process.
            host = subprocess.Popen([sys.executable, '-m', 'runespeak.bridge', '--native-host'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            host.stdin.write(struct.pack('=I', len(payload)) + payload)
            host.stdin.close()
            pump(root, lambda: host.poll() is not None)
            reply = host.stdout.read()
            assert json.loads(reply[4:])['ok'], reply
            host.stdout.close()
            host.stderr.close()
            pump(root, lambda: app.player is not None or bool(errors))
            assert not errors, errors
            assert app.busy, 'Playback did not start until after all synthesis completed'
            assert app.stream.cursor == 1
            assert app.reader.tag_ranges('speaking'), 'Current passage is not highlighted'
            assert app.pages.select() == str(app.reader_page)
            print('First section started before generation finished; read-along passage highlighted', flush=True)
            app.pause()
            assert app.paused
            cursor = app.stream.cursor
            pump(root, lambda: not app.busy or bool(errors))
            assert not errors, errors
            assert app.paused and app.stream.cursor == cursor
            assert app.stream.complete and len(app.stream.parts) > 1
            with tempfile.TemporaryDirectory() as folder:
                saved = Path(folder) / 'narration.wav'
                with patch('runespeak.app.filedialog.asksaveasfilename', return_value=str(saved)):
                    app.save()
                frames = 0
                for part in app.stream.parts:
                    with wave.open(str(part), 'rb') as audio:
                        frames += audio.getnframes()
                with wave.open(str(saved), 'rb') as audio:
                    assert audio.getnframes() == frames
                assert json.loads(saved.with_suffix('.json').read_text())['text'] == text.strip()
            before = app.clock.snapshot()[0]
            app.seek_relative(10)
            assert app.paused
            assert abs(app.clock.snapshot()[0] - min(before+10, app.total_seconds)) < .1
            app.seek_relative(-10)
            assert app.paused
            print('Real audio ±10-second seeking preserves Pause', flush=True)
            app.play()
            assert not app.paused
            app.restart()
            assert app.stream.cursor == 1
            app.stop()
            assert app.player is None and not app.stream.active
            assert not app.reader.tag_ranges('speaking')
            print('Pause holds across incoming sections; resume, restart, stop and full WAV save passed', flush=True)
        finally:
            app.close()
        assert not app.bridge.path.exists()
        print('Native selection delivery and app-close socket cleanup passed', flush=True)


if __name__ == '__main__':
    main()
