"""Speech engine settings: this device or a local AI server, with an A/B comparison."""
import json
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .backends import (LOCAL, SERVER, DEFAULT_TIMEOUT, ServerEngine, ServerError, engine_settings,
                       load_token, normalize_url, save_token, test_connection)
from .platforms import AudioPlayer, hidden_window_flags, player_command, python_command

SAMPLE = ('Rest a moment, adventurer. Set down your pack, and let the tome carry the words '
          'from here, one clear sentence at a time.')


def local_timing(text, voice, folder, runner=subprocess.run):
    """Synthesize ``text`` in a worker process on this device. Returns (path, synth s, audio s).
    A short warm-up phrase goes first so model loading is not counted."""
    directory = Path(folder)
    job = directory / 'compare-local.json'
    job.write_text(json.dumps({'directory': str(directory), 'voice': voice, 'chunks': ['Ready.', text],
                               'engine': {'kind': LOCAL}}))
    result = runner(python_command('runespeak.worker', job), capture_output=True, text=True,
                    creationflags=hidden_window_flags(), timeout=600)
    if result.returncode:
        raise RuntimeError((result.stderr or 'Local synthesis failed')[-800:])
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == 'timing' and parts[1] == '1':
            target = directory / 'A-this-device.wav'
            (directory / '00001.wav').replace(target)
            return target, float(parts[2]), float(parts[3])
    raise RuntimeError('Local synthesis gave no timing.')


def server_timing(engine, text, voice, folder):
    started = time.monotonic()
    payload, seconds, server_seconds = engine.synthesize(text, voice)
    took = time.monotonic() - started
    target = Path(folder) / 'B-server.wav'
    target.write_bytes(payload)
    return target, took, seconds, server_seconds


def describe(label, took, seconds, extra=''):
    rtf = took / seconds if seconds else float('nan')
    return f'{label}: {took:.2f} s to make {seconds:.2f} s of speech (real-time factor {rtf:.2f}){extra}'


class EngineDialog:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        settings = engine_settings(app.preferences)
        self.saved_token = load_token()
        self.window = tk.Toplevel(self.root)
        self.window.title('Speech Engine')
        self.window.transient(self.root)
        self.window.resizable(False, False)
        self.kind = tk.StringVar(value=settings['kind'])
        self.url = tk.StringVar(value=settings['url'])
        self.token = tk.StringVar()
        self.timeout = tk.StringVar(value=str(int(settings['timeout'])))
        self.result = tk.StringVar(value='')
        self.compare_a = tk.StringVar(value='')
        self.compare_b = tk.StringVar(value='')
        self.files = {}
        self.player = None
        self.folder = tempfile.TemporaryDirectory(prefix='runespeak-compare-')
        frame = ttk.Frame(self.window, padding=14)
        frame.grid(sticky='nsew')
        ttk.Radiobutton(frame, text='This device (built in, no setup)', value=LOCAL, variable=self.kind,
                        command=self.update_state).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Radiobutton(frame, text='Local AI server (advanced)', value=SERVER, variable=self.kind,
                        command=self.update_state).grid(row=1, column=0, columnspan=3, sticky='w', pady=(4, 8))
        ttk.Label(frame, text='Server address').grid(row=2, column=0, sticky='w')
        self.url_entry = ttk.Entry(frame, textvariable=self.url, width=42)
        self.url_entry.grid(row=2, column=1, columnspan=2, sticky='ew', pady=2)
        ttk.Label(frame, text='Access token').grid(row=3, column=0, sticky='w')
        self.token_entry = ttk.Entry(frame, textvariable=self.token, width=42, show='•')
        self.token_entry.grid(row=3, column=1, columnspan=2, sticky='ew', pady=2)
        self.token_hint = ttk.Label(frame, text=self.token_hint_text(), wraplength=420)
        self.token_hint.grid(row=4, column=1, columnspan=2, sticky='w')
        ttk.Label(frame, text='Timeout (seconds)').grid(row=5, column=0, sticky='w')
        self.timeout_entry = ttk.Spinbox(frame, from_=2, to=300, textvariable=self.timeout, width=6)
        self.timeout_entry.grid(row=5, column=1, sticky='w', pady=2)
        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky='w', pady=(8, 2))
        self.test_button = ttk.Button(buttons, text='Test connection', command=self.test)
        self.test_button.grid(row=0, column=0, padx=(0, 6))
        self.forget_button = ttk.Button(buttons, text='Forget saved token', command=self.forget_token)
        self.forget_button.grid(row=0, column=1)
        ttk.Label(frame, textvariable=self.result, wraplength=460).grid(row=7, column=0, columnspan=3, sticky='w', pady=(4, 8))
        ttk.Separator(frame).grid(row=8, column=0, columnspan=3, sticky='ew', pady=6)
        ttk.Label(frame, text='Compare engines on the selected text (or the first passage):').grid(row=9, column=0, columnspan=3, sticky='w')
        self.compare_button = ttk.Button(frame, text='Compare A/B', command=self.compare)
        self.compare_button.grid(row=10, column=0, sticky='w', pady=4)
        self.play_a = ttk.Button(frame, text='Play A', command=lambda: self.play('A'), state='disabled')
        self.play_a.grid(row=10, column=1, sticky='w')
        self.play_b = ttk.Button(frame, text='Play B', command=lambda: self.play('B'), state='disabled')
        self.play_b.grid(row=10, column=2, sticky='w')
        ttk.Label(frame, textvariable=self.compare_a, wraplength=460).grid(row=11, column=0, columnspan=3, sticky='w')
        ttk.Label(frame, textvariable=self.compare_b, wraplength=460).grid(row=12, column=0, columnspan=3, sticky='w')
        actions = ttk.Frame(frame)
        actions.grid(row=13, column=0, columnspan=3, sticky='e', pady=(12, 0))
        ttk.Button(actions, text='Cancel', command=self.close).grid(row=0, column=0, padx=6)
        ttk.Button(actions, text='Save', command=self.save).grid(row=0, column=1)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Escape>', lambda _event: self.close())
        self.update_state()

    def token_hint_text(self):
        return ('A token is saved. Leave this blank to keep it.' if self.saved_token
                else 'Optional. Stored in your keychain when available, otherwise in a private file.')

    def update_state(self):
        state = 'normal' if self.kind.get() == SERVER else 'disabled'
        for widget in (self.url_entry, self.token_entry, self.timeout_entry, self.test_button):
            widget.configure(state=state)
        self.forget_button.configure(state='normal' if self.saved_token else 'disabled')

    def current_token(self):
        return self.token.get().strip() or self.saved_token

    def current_timeout(self):
        try:
            return min(300.0, max(2.0, float(self.timeout.get())))
        except ValueError:
            return DEFAULT_TIMEOUT

    def run_in_background(self, work, done):
        def runner():
            try:
                value = work()
            except Exception as exc:          # Messages never contain the token.
                value = exc
            self.root.after(0, lambda: done(value))
        threading.Thread(target=runner, daemon=True).start()

    def test(self):
        try:
            url = normalize_url(self.url.get())
        except ValueError as exc:
            self.result.set(str(exc))
            return
        self.result.set('Testing…')
        self.test_button.configure(state='disabled')
        token, timeout = self.current_token(), self.current_timeout()

        def done(value):
            self.test_button.configure(state='normal')
            self.result.set(str(value))
        self.run_in_background(lambda: test_connection(url, token, timeout), done)

    def forget_token(self):
        save_token('')
        self.saved_token = ''
        self.token.set('')
        self.token_hint.configure(text=self.token_hint_text())
        self.update_state()
        self.result.set('The saved token was removed.')

    def passage(self):
        app = self.app
        try:
            text = app.text.get('sel.first', 'sel.last').strip()
        except tk.TclError:
            text = ''
        if not text:
            from .core import chunks
            from .narration import prepare_reading
            body = app.text.get('1.0', 'end').strip()
            pieces = chunks(prepare_reading(body).text) if body else []
            text = next((piece for piece in pieces if len(piece) > 60), pieces[0] if pieces else SAMPLE)
        return text[:600]

    def compare(self):
        try:
            url = normalize_url(self.url.get())
        except ValueError as exc:
            self.compare_b.set(str(exc))
            return
        text, voice = self.passage(), self.app.voice.get()
        token, timeout = self.current_token(), self.current_timeout()
        folder = self.folder.name
        self.compare_button.configure(state='disabled')
        self.compare_a.set('A · This device: working…')
        self.compare_b.set('B · AI server: working…')

        def work_a():
            path, took, seconds = local_timing(text, voice, folder)
            return path, describe('A · This device', took, seconds)

        def work_b():
            engine = ServerEngine(url, token, timeout)
            path, took, seconds, server_seconds = server_timing(engine, text, voice, folder)
            extra = f'; the server itself took {server_seconds:.2f} s' if server_seconds == server_seconds else ''
            return path, describe('B · AI server', took, seconds, extra)

        remaining = {'count': 2}

        def finished(slot, variable, button):
            def done(value):
                if isinstance(value, Exception):
                    variable.set(f'{slot} failed: {value}')
                else:
                    self.files[slot] = value[0]
                    variable.set(value[1])
                    button.configure(state='normal')
                remaining['count'] -= 1
                if not remaining['count']:
                    self.compare_button.configure(state='normal')
            return done
        # B first so the server is not slowed by this device's work, then A.
        self.run_in_background(work_b, finished('B', self.compare_b, self.play_b))
        self.run_in_background(work_a, finished('A', self.compare_a, self.play_a))

    def play(self, slot):
        self.stop_player()
        path = self.files.get(slot)
        if path and Path(path).exists():
            self.player = AudioPlayer(path, command=player_command())

    def stop_player(self):
        if self.player and self.player.poll() is None:
            self.player.terminate()
        self.player = None

    def save(self):
        kind = self.kind.get()
        url = self.url.get().strip()
        if kind == SERVER:
            try:
                url = normalize_url(url)
            except ValueError as exc:
                self.result.set(str(exc))
                return
        if self.token.get().strip():
            save_token(self.token.get())
        self.app.preferences['engine'] = {'kind': kind, 'url': url, 'timeout': self.current_timeout()}
        self.app.save_preferences_quietly()
        self.app.status.set('Speech engine: ' + ('local AI server at ' + url if kind == SERVER else 'this device') + '.')
        self.close()

    def close(self):
        self.stop_player()
        try:
            self.folder.cleanup()
        except OSError:
            pass
        self.window.destroy()
        self.app.engine_dialog = None
