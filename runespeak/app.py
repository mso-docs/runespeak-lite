"""Small native Linux narrator. All Tk calls stay on the UI thread."""
import json
from importlib.resources import files
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from urllib.parse import urlparse

from .core import chunks, combine
from .saving import save_directory, filename, unused_name
from .narration import prepare_reading
from .widgets import Hint
from .follow import passage_at
from .preferences import load_preferences, save_preferences
from .navigation import locate_time, audio_slice, headings, heading_targets, reading_context
from .documents import load_source, SUPPORTED
from .streaming import SectionQueue
from .timing import ListeningClock, duration, format_time
from .bridge import CommandServer, request
from .tome import TomeSurface, configure_theme, INK


class App:
    def __init__(self, root, enable_bridge=True):
        self.root = root
        root.title('Runespeak Lite')
        # Tk's default window icon is a generic gear under GNOME/XWayland.
        # Embed a PNG as well as supplying the launcher icon, including for CLI launches.
        with files('runespeak').joinpath('resources/icon.png').open('rb') as icon_file:
            import base64
            self.window_icon = tk.PhotoImage(master=root, data=base64.b64encode(icon_file.read()).decode('ascii'))
        root.iconphoto(True, self.window_icon)
        root.iconname('Runespeak Lite')
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width = min(1440, max(960, int(screen_w * .65)), screen_w-60)
        height = min(1160, max(800, int(screen_h * .85)), screen_h-100)
        root.geometry(f'{width}x{height}')
        root.minsize(min(960, width), min(800, height))
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.worker = None
        self.player = None
        self.playback_log = None
        self.paused = False
        self.closed = False
        self.busy = False
        self.audio = None
        self.stream = None
        self.recorded_input = None
        self.clock = ListeningClock()
        self.total_seconds = 0.0
        self.total_estimated = False
        self.section_durations = []
        self.section_words = []
        self.timer_text = tk.StringVar(value="0:00 / —")
        self.bridge = None
        self.voice_catalog = json.loads(files('runespeak').joinpath('resources/voices.json').read_text())
        self.preferences = load_preferences()
        selected = self.preferences.get('voice', 'default')
        self.voice = tk.StringVar(master=root, value=selected if selected in {'default', *(v['name'] for v in self.voice_catalog)} else 'default')
        self.preview_player = None
        self.preview_loading = False
        self.preview_log = None
        self.seek_file = None
        self.playback_continuation = None
        self.pending_reading = None
        self.heading_targets = []
        self.playback_passage_offset = 0
        self.follow_ranges = []
        self.follow_durations = []
        self.follow_active = None
        self.highlight_color = '#dcc57c'
        self.follow_scroll = tk.BooleanVar(master=root, value=True)
        self.highlight_enabled = tk.BooleanVar(master=root, value=True)
        self.temp = tempfile.TemporaryDirectory(prefix='runespeak-')
        self.url = tk.StringVar()
        self.status = tk.StringVar(value='Rest a moment, adventurer. Inscribe a passage, then awaken the tome.')
        serif, font_size = configure_theme(root)
        self.surface = TomeSurface(root, serif, font_size)
        outer = self.surface.content
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        ttk.Label(outer, text='I. SUMMON A PASSAGE', style='Chapter.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 5))
        bar = ttk.Frame(outer, style='Tome.TFrame')
        bar.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        ttk.Entry(bar, textvariable=self.url, style='Tome.TEntry', font=(serif, font_size(15))).pack(side='left', fill='x', expand=True)
        self.load_button = ttk.Button(bar, text='Load link', command=self.load_page, style='Tome.TButton')
        self.load_button.pack(side='left', padx=(8, 0))
        inscription = ttk.Frame(outer, style='Tome.TFrame')
        inscription.grid(row=2, column=0, sticky='ew')
        ttk.Label(inscription, text='THE INSCRIBED WORDS', style='Chapter.TLabel').pack(side='left')
        ttk.Button(inscription, text='Open document…', command=self.open_document, style='Tome.TButton').pack(side='right', padx=(8, 0))
        ttk.Button(inscription, text='Paste copied text', command=self.paste, style='Tome.TButton').pack(side='right')
        self.pages = ttk.Notebook(outer, style='Tome.TNotebook')
        self.pages.grid(row=3, column=0, sticky='nsew', pady=(8, 10))
        source_page = ttk.Frame(self.pages, style='Tome.TFrame')
        self.reader_page = ttk.Frame(self.pages, style='Tome.TFrame')
        self.pages.add(source_page, text='Source')
        self.pages.add(self.reader_page, text='Read along')
        text_style = dict(wrap='word', padx=17, pady=13, width=20, height=7,
                          background='#f2e3c3', foreground=INK, insertbackground=INK,
                          selectbackground='#b4c0a1', selectforeground='#202d22',
                          font=(serif, font_size(17)), spacing1=3, spacing3=6,
                          relief='flat', highlightthickness=0, borderwidth=0)
        self.text = tk.Text(source_page, undo=True, **text_style)
        self.reader = tk.Text(self.reader_page, state='disabled', **text_style)
        self.reader.tag_configure('speaking', background=self.highlight_color, foreground=INK)
        for page, widget in ((source_page, self.text), (self.reader_page, self.reader)):
            scroll = ttk.Scrollbar(page, command=widget.yview, style='Tome.Vertical.TScrollbar')
            widget.configure(yscrollcommand=scroll.set)
            scroll.pack(side='right', fill='y')
            widget.pack(side='left', fill='both', expand=True)
        self.prepare_follow([])
        reading_options = ttk.Frame(outer, style='Tome.TFrame')
        reading_options.grid(row=4, column=0, sticky='ew', pady=(0, 5))
        ttk.Label(reading_options, text='II. AWAKEN THE SPEAKING TOME', style='Chapter.TLabel').pack(anchor='w')
        self.include_backmatter = tk.BooleanVar(master=root, value=False)
        ttk.Checkbutton(reading_options, text='Include references, appendices & full author lists', variable=self.include_backmatter, style='Tome.TCheckbutton').pack(anchor='w', pady=(4, 0))
        controls = ttk.Frame(outer, style='Tome.TFrame')
        controls.grid(row=5, column=0, sticky='ew')
        self.buttons = {}
        self.control_icons = {}
        for column, (key, icon, action, description) in enumerate([
            ('Back 10s', 'back10', lambda: self.seek_relative(-10), 'Back 10 seconds'),
            ('Play / Resume', 'play', self.play, 'Play — read this passage or resume'),
            ('Pause', 'pause', self.pause, 'Pause'),
            ('Stop', 'stop', self.stop, 'Stop'),
            ('Forward 10s', 'forward10', lambda: self.seek_relative(10), 'Forward 10 seconds'),
            ('Restart', 'restart', self.restart, 'Restart from the beginning'),
        ]):
            import base64
            image = tk.PhotoImage(master=root, data=base64.b64encode(files('runespeak').joinpath(f'resources/{icon}.png').read_bytes()).decode('ascii'))
            self.control_icons[key] = image
            button = ttk.Button(controls, text=description, compound='image', image=image, command=action, style='Seal.TButton', takefocus=True)
            button.grid(row=0, column=column, padx=(0, 8), pady=(4, 10), sticky='w')
            Hint(button, description)
            self.buttons[key] = button
        self.highlighter_icon = tk.PhotoImage(master=root, data=base64.b64encode(files('runespeak').joinpath('resources/highlight.png').read_bytes()).decode('ascii'))
        highlighter = ttk.Button(controls, text='Highlighter colors and follow options', compound='image', image=self.highlighter_icon, command=self.highlight_menu, style='Seal.TButton')
        highlighter.grid(row=0, column=8, sticky='e', pady=(4, 10))
        Hint(highlighter, 'Highlighter — colors and automatic scrolling')
        self.highlighter_button = highlighter
        self.narrator_icon = tk.PhotoImage(master=root, data=base64.b64encode(files('runespeak').joinpath('resources/narrator.png').read_bytes()).decode('ascii'))
        self.narrator_button = ttk.Button(controls, text='Choose your narrator', compound='image', image=self.narrator_icon, command=self.narrator_menu, style='Seal.TButton')
        self.narrator_button.grid(row=0, column=6, sticky='e', pady=(4, 10), padx=(0, 8))
        Hint(self.narrator_button, lambda: 'Choose your narrator — ' + self.voice.get())
        self.navigation_icon = tk.PhotoImage(master=root, data=base64.b64encode(files('runespeak').joinpath('resources/navigate.png').read_bytes()).decode('ascii'))
        self.navigation_button = ttk.Button(controls, text='Read from here and jump to headings', compound='image', image=self.navigation_icon, command=self.navigation_menu, style='Seal.TButton')
        self.navigation_button.grid(row=0, column=7, sticky='e', pady=(4, 10), padx=(0, 8))
        Hint(self.navigation_button, 'Bookmark — read from selection or jump to a heading')
        for widget in (self.text, self.reader):
            widget.bind('<Button-3>', self.navigation_context)
            widget.bind('<Control-Return>', lambda event: (self.read_from_here(), 'break')[1])
        root.bind('<Alt-Left>', lambda event: (self.seek_relative(-10), 'break')[1])
        root.bind('<Alt-Right>', lambda event: (self.seek_relative(10), 'break')[1])
        tools = ttk.Frame(controls, style='Tome.TFrame')
        tools.grid(row=1, column=0, columnspan=9, sticky='ew')
        for column, (label, action) in enumerate([('Save WAV', self.save), ('Open WAV', self.open_audio), ('Preview reading', self.preview_reading)]):
            button = ttk.Button(tools, text=label, command=action, style='Tome.TButton')
            button.grid(row=0, column=column, sticky='ew', padx=(0, 8))
            tools.columnconfigure(column, weight=1, uniform='tools')
            self.buttons[label] = button
        controls.columnconfigure(6, weight=1)
        self.generate = self.buttons['Play / Resume']
        self.progress = ttk.Progressbar(outer, mode='indeterminate', style='Tome.Horizontal.TProgressbar')
        self.progress.grid(row=6, column=0, sticky='ew', pady=(4, 0))
        timer = ttk.Label(outer, textvariable=self.timer_text, style='Timer.TLabel', takefocus=True)
        timer.grid(row=7, column=0, sticky='w', pady=(8, 0))
        Hint(timer, lambda: 'Elapsed / total · ~ means estimated\nTotal listened this sitting: ' + format_time(self.clock.snapshot()[1]))
        ttk.Label(outer, textvariable=self.status, wraplength=800, style='Tome.TLabel').grid(row=8, column=0, sticky='ew', pady=(8, 0))
        self.dnd_enabled = False
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES, DND_TEXT
            TkinterDnD._require(root)
            for target in (self.surface, self.text, self.reader):
                target.drop_target_register(DND_FILES, DND_TEXT)
                target.dnd_bind('<<Drop>>', self.drop_document)
            self.dnd_enabled = True
        except (ImportError, tk.TclError, RuntimeError):
            self.status.set('Drag-and-drop unavailable here. Use Open document or Load link.')
        if enable_bridge:
            self.bridge = CommandServer(self.enqueue_command)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def paste(self):
        try:
            value = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showinfo('Clipboard', 'Copy highlighted text first (Ctrl+C), then paste here.')
            return
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', value)
        self.url.set('')
        self.pages.select(0)
        self.status.set('Text pasted. Click Play to generate and play narration.')

    def set_busy(self, busy):
        self.busy = busy
        state = 'disabled' if busy else 'normal'
        self.generate.configure(state=state)
        self.load_button.configure(state=state)
        self.progress.stop()
        self.progress.configure(mode='determinate')

    def open_document(self):
        if self.busy:
            self.status.set('Stop the current job before loading another document.')
            return
        paths = filedialog.askopenfilenames(title='Open documents for the speaking tome', filetypes=[
            ('Readable documents', ' '.join('*'+suffix for suffix in SUPPORTED)), ('All files', '*')])
        if paths:
            self.load_documents(list(paths))

    def drop_document(self, event):
        if self.busy:
            self.status.set('Stop the current job before dropping another document.')
            return 'none'
        data = event.data.strip()
        if data.startswith(('http://', 'https://', 'file://')):
            sources = [line.strip() for line in data.splitlines() if line.strip() and not line.startswith('#')]
        else:
            sources = list(self.root.tk.splitlist(data))
        if sources:
            self.load_documents(sources)
        return 'copy'

    def load_page(self):
        source = self.url.get().strip()
        if source:
            self.load_documents([source])
        else:
            self.status.set('Enter a webpage or document URL, or choose Open document.')

    def load_documents(self, sources):
        if self.busy:
            return
        if len(sources) > 20:
            messagebox.showerror('Open documents', 'Load at most 20 documents at a time.')
            return
        self.cancel.clear()
        self.set_busy(True)
        self.status.set('Opening the manuscript…')
        def work():
            try:
                sections = []
                for source in sources:
                    if self.cancel.is_set():
                        raise InterruptedError('Document loading cancelled')
                    text = load_source(source, self.cancel, lambda message: self.events.put(('loading', message)))
                    sections.append(text)
                text = '\n\n'.join(sections)
                if len(text.encode('utf-8')) > 4*1024*1024:
                    raise ValueError('Combined documents exceed the 4 MB text limit.')
                source = sources[0]
                if urlparse(source).scheme not in ('http', 'https', 'file'):
                    source = Path(source).expanduser().resolve().as_uri()
                self.events.put(('document', (text, source)))
            except Exception as exc:
                if not self.cancel.is_set():
                    self.events.put(('load_error', str(exc)))
            finally:
                self.events.put(('idle', None))
                if self.closed:
                    self.temp.cleanup()
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def preview_reading(self):
        reading = prepare_reading(self.text.get('1.0', 'end').strip(), self.include_backmatter.get())
        window = tk.Toplevel(self.root)
        window.title('Preview the spoken words')
        window.geometry('800x600')
        ttk.Label(window, text=reading.summary(), wraplength=740).pack(fill='x', padx=12, pady=12)
        preview = tk.Text(window, wrap='word', padx=12, pady=12)
        scroll = ttk.Scrollbar(window, command=preview.yview)
        preview.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        preview.pack(fill='both', expand=True)
        preview.insert('1.0', reading.text)
        preview.configure(state='disabled')

    def narrate(self, text_override=None):
        self.stop_preview()
        if self.busy:
            return
        full_text = self.text.get('1.0', 'end').strip()
        text = full_text if text_override is None else text_override
        source_url = self.url.get().strip()
        reading = prepare_reading(text, self.include_backmatter.get())
        pieces = chunks(reading.text)
        display_pieces, playback_offset = pieces, 0
        if text_override is not None:
            full_reading = prepare_reading(full_text, self.include_backmatter.get())
            display_pieces, pieces, playback_offset = reading_context(full_reading.text, reading.text, chunks)
        if not pieces:
            self.status.set('Paste text or load a webpage, then click Play.')
            return
        self.prepare_follow(display_pieces)
        self.playback_passage_offset = playback_offset
        self.pages.select(self.reader_page)
        voice = self.voice.get()
        self.recorded_input = (full_text, self.include_backmatter.get(), voice)
        self.stop_player()
        self.cancel.clear()
        self.set_busy(True)
        self.status.set('The tome stirs… Preparing its voice. The first invocation downloads the model.')
        self.audio = None
        self.stream = SectionQueue()
        self.clock.reset()
        self.section_durations = []
        self.section_words = [len(piece.split()) for piece in pieces]
        self.total_seconds = sum(self.section_words) / 2.5
        self.total_estimated = True
        # Old temporary audio is no longer needed after starting another narration.
        for child in Path(self.temp.name).iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        def work():
            try:
                directory = Path(tempfile.mkdtemp(dir=self.temp.name))
                job = directory / 'job.json'
                job.write_text(json.dumps({'chunks': pieces, 'directory': str(directory), 'voice': voice}))
                with (directory / 'errors.log').open('w+') as errors:
                    with self.lock:
                        if self.cancel.is_set():
                            return
                        process = subprocess.Popen([sys.executable, '-m', 'runespeak.worker', str(job)], stdout=subprocess.PIPE, stderr=errors, text=True)
                        self.process = process
                    for line in process.stdout:
                        if line.strip().isdigit():
                            count = int(line.strip())
                            self.events.put(('section', (directory / f'{count - 1:05d}.wav', count, len(pieces))))
                    process.stdout.close()
                    code = process.wait()
                    if self.cancel.is_set():
                        return
                    if code:
                        errors.seek(0)
                        raise RuntimeError(errors.read()[-3000:] or 'Phonon generation failed')
                output = Path(self.temp.name) / f'narration-{time.time_ns()}.wav'
                combine(sorted(directory.glob('*.wav')), output)
                output.with_suffix('.json').write_text(json.dumps({'text': text, 'context_text': full_text, 'read_along': display_pieces, 'playback_passage_offset': playback_offset, 'spoken_text': reading.text, 'narration_cleanup': reading.summary(), 'sections': [{'text': piece, 'duration': duration(directory / f'{index:05d}.wav') or 0} for index, piece in enumerate(pieces)], 'source': source_url, 'model': 'gradium/phonon-31m-en', 'voice': voice}, indent=2))
                self.events.put(('audio', output))
            except Exception as exc:
                if not self.cancel.is_set():
                    self.events.put(('error', str(exc)))
            finally:
                with self.lock:
                    self.process = None
                self.events.put(('idle', None))
                if self.closed:
                    self.temp.cleanup()
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def stop_player(self):
        self.clock.pause()
        self.playback_continuation = None
        if self.player and self.player.poll() is None:
            self.player.send_signal(signal.SIGCONT)
            self.player.terminate()
            try:
                self.player.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.player.kill()
                self.player.wait()
        self.player = None
        if self.seek_file:
            self.seek_file.unlink(missing_ok=True)
            self.seek_file = None
        if self.playback_log:
            self.playback_log.close()
            self.playback_log = None
        self.paused = False

    def start_file(self, path, seek=0):
        command = next((p for p in ['pw-play', 'paplay', 'aplay'] if shutil.which(p)), None)
        if not command:
            self.playback_failed('Install a PipeWire, PulseAudio, or ALSA command-line player.')
            return
        try:
            original_path = Path(path)
            length = duration(path) or 0
            offset = sum(self.section_durations[:self.stream.cursor - 1]) if self.stream else 0
            if seek > 0:
                self.seek_file = Path(self.temp.name) / f'seek-{time.time_ns()}.wav'
                length = audio_slice(path, self.seek_file, seek, maximum=max(0, length-seek) if self.stream else 30)
                path = self.seek_file
                if not self.stream and seek+length < (duration(original_path) or 0)-.001:
                    self.playback_continuation = (original_path, seek+length)
            self.playback_log = tempfile.TemporaryFile(mode='w+')
            self.player = subprocess.Popen([command, str(path)], stderr=self.playback_log)
            self.clock.start(offset+seek, length)
            self.update_follow()
        except (OSError, ValueError) as exc:
            self.playback_failed(str(exc))

    def playback_failed(self, details):
        self.stop_player()
        if self.stream:
            if self.stream.active and self.stream.cursor:
                self.stream.cursor -= 1
            self.stream.active = False
        self.status.set('Playback failed. You can still save completed narration.')
        messagebox.showerror('Playback failed', details)

    def next_section(self):
        if not self.stream or self.player or self.paused:
            return
        part = self.stream.next()
        if part:
            self.start_file(part)
            if self.player:
                self.status.set('Playing — preparing more passages…' if self.busy else 'Playing')
        elif self.stream.active:
            if self.stream.complete:
                self.stream.active = False
                self.status.set('Finished')
            else:
                self.status.set('Waiting for the next section…')

    def play(self):
        if self.preview_loading:
            self.status.set('Preparing a narrator preview. Stop it before starting narration.')
            return
        self.stop_preview()
        current = (self.text.get('1.0', 'end').strip(), self.include_backmatter.get(), self.voice.get())
        if not self.busy and self.recorded_input is not None and current != self.recorded_input:
            self.narrate()
            return
        if self.player and self.player.poll() is None:
            if self.paused:
                self.player.send_signal(signal.SIGCONT)
                self.paused = False
                self.clock.resume()
                self.status.set('Playing')
            return
        if self.stream:
            self.paused = False
            self.stream.active = True
            if self.stream.complete and self.stream.cursor >= len(self.stream.parts):
                self.stream.restart()
            self.next_section()
            return
        if self.busy:
            self.status.set('Loading webpage…')
            return
        if not self.audio:
            self.narrate()
            return
        self.start_file(self.audio)
        if self.player:
            self.status.set('Playing')

    def pause(self):
        self.clock.pause()
        if self.player and self.player.poll() is None:
            self.player.send_signal(signal.SIGSTOP)
        if self.player or (self.stream and self.stream.active):
            self.paused = True
            self.status.set('Paused')

    def restart(self):
        self.stop_player()
        if self.stream:
            self.stream.restart()
            self.next_section()
        elif self.audio:
            self.start_file(self.audio)
        else:
            self.play()

    def stop(self):
        self.pending_reading = None
        self.cancel.set()
        self.stop_preview()
        with self.lock:
            if self.process and self.process.poll() is None:
                process = self.process
                process.terminate()
                def reap():
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                threading.Thread(target=reap, daemon=True).start()
        self.stop_player()
        if self.stream:
            self.stream.active = False
            if not self.stream.complete:
                self.stream = None
            else:
                self.stream.cursor = 0
        self.clear_follow()
        self.status.set('Stopped')

    def save(self):
        if not self.audio:
            self.status.set('Click Play first. Save WAV becomes available after generation.')
            return
        try:
            directory = save_directory()
            directory.mkdir(parents=True, exist_ok=True)
            metadata = self.audio.with_suffix('.json')
            info = {}
            if metadata.exists():
                try:
                    info = json.loads(metadata.read_text())
                    if not isinstance(info, dict):
                        info = {}
                except (OSError, ValueError):
                    pass
            # Use the completed recording's metadata, not a subsequently edited URL.
            name = filename(str(info.get('source', '')), str(info.get('text', '')), self.audio.stem)
            name = unused_name(directory, name)
            path = filedialog.asksaveasfilename(
                defaultextension='.wav', filetypes=[('WAV audio', '*.wav')],
                initialdir=str(directory), initialfile=name)
            if path:
                target = Path(path)
                if target.resolve() != self.audio.resolve():
                    shutil.copyfile(self.audio, target)
                    if metadata.exists():
                        shutil.copyfile(metadata, target.with_suffix('.json'))
                self.status.set(f'Saved {path}')
        except OSError as exc:
            messagebox.showerror('Save failed', str(exc))

    def open_audio(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(filetypes=[('WAV audio', '*.wav')])
        if path:
            try:
                self.load_audio(path)
            except ValueError as exc:
                messagebox.showerror('Open narration', str(exc))

    def load_audio(self, path):
        if self.busy:
            raise ValueError('Stop the current generation or webpage load first.')
        audio = Path(path).expanduser().resolve()
        seconds = duration(audio)
        if seconds is None:
            raise ValueError('This file is not a readable WAV narration.')
        self.stop_player()
        self.audio = audio
        self.recorded_input = (self.text.get('1.0', 'end').strip(), self.include_backmatter.get(), self.voice.get())
        self.stream = None
        self.clock.reset()
        self.total_seconds = seconds
        self.total_estimated = False
        self.section_durations = []
        self.prepare_follow([])
        metadata = audio.with_suffix('.json')
        if metadata.exists():
            try:
                info = json.loads(metadata.read_text())
                context = info.get('context_text', info.get('text'))
                if isinstance(context, str):
                    self.text.delete('1.0', 'end')
                    self.text.insert('1.0', context)
                    self.url.set(info.get('source', '') if isinstance(info.get('source', ''), str) else '')
                    self.recorded_input = (self.text.get('1.0', 'end').strip(), self.include_backmatter.get(), self.voice.get())
                sections = info.get('sections', [])
                if sections and all(isinstance(part.get('text'), str) and isinstance(part.get('duration'), (int, float)) and part['duration'] >= 0 for part in sections):
                    if abs(sum(part['duration'] for part in sections) - seconds) < .25:
                        display = info.get('read_along', [part['text'] for part in sections])
                        offset = info.get('playback_passage_offset', 0)
                        if not isinstance(display, list) or not all(isinstance(piece, str) for piece in display) or not isinstance(offset, int) or offset < 0 or offset+len(sections) > len(display):
                            display, offset = [part['text'] for part in sections], 0
                        self.prepare_follow(display)
                        self.playback_passage_offset = offset
                        self.follow_durations = [part['duration'] for part in sections]
                        self.pages.select(self.reader_page)
            except (OSError, ValueError, TypeError, AttributeError):
                pass  # Plain WAVs remain playable without a matching sidecar.
        self.status.set('Saved narration loaded. Press Play.')

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'command':
                    message, done, response = value
                    if not done.is_set():
                        try:
                            response.update(self.handle_command(message))
                        except Exception as exc:
                            response.update({'ok': False, 'error': str(exc)})
                        done.set()
                elif kind == 'voice_preview' and not self.cancel.is_set():
                    player = next((p for p in ('pw-play', 'paplay', 'aplay') if shutil.which(p)), None)
                    if player:
                        try:
                            self.preview_log = tempfile.TemporaryFile(mode='w+')
                            self.preview_player = subprocess.Popen([player, str(value)], stderr=self.preview_log)
                            self.status.set('Previewing narrator — ' + self.preview_voice_name)
                        except OSError as exc:
                            self.stop_preview()
                            messagebox.showerror('Narrator preview', str(exc))
                    else:
                        messagebox.showerror('Narrator preview', 'Install pw-play, paplay or aplay.')
                elif kind == 'idle':
                    self.preview_loading = False
                    self.set_busy(False)
                    if self.pending_reading is not None:
                        text, self.pending_reading = self.pending_reading, None
                        self.narrate(text_override=text)
                elif kind == 'load_error':
                    self.status.set('Could not load this manuscript. Existing narration is still available.')
                    messagebox.showerror('Open document', value)
                elif kind == 'error':
                    if self.stream:
                        self.stop_player()
                        self.stream = None
                    self.status.set('Unable to complete request')
                    messagebox.showerror('Runespeak', value)
                elif kind == 'loading' and not self.cancel.is_set():
                    self.status.set(value)
                elif kind == 'document' and not self.cancel.is_set():
                    text, source = value
                    self.text.delete('1.0', 'end')
                    self.text.insert('1.0', text)
                    self.pages.select(0)
                    self.url.set(source)
                    self.status.set('Manuscript ready. Review the text, then Play. OCR and PDF reading order may need corrections.')
                elif kind == 'page' and not self.cancel.is_set():
                    self.text.delete('1.0', 'end')
                    self.text.insert('1.0', value)
                    self.pages.select(0)
                    self.status.set('Article loaded. Click Play to generate and play narration.')
                elif kind == 'section' and not self.cancel.is_set() and self.stream:
                    part, count, total = value
                    self.stream.parts.append(part)
                    self.section_durations.append(duration(part) or 0)
                    self.follow_durations = list(self.section_durations)
                    generated_words = sum(self.section_words[:count])
                    if generated_words:
                        self.total_seconds = sum(self.section_durations) * sum(self.section_words) / generated_words
                    if not self.paused and not self.player:
                        self.status.set('Preparing the next passage…')
                    self.next_section()
                elif kind == 'audio' and not self.cancel.is_set():
                    self.audio = value
                    self.total_seconds = duration(value) or sum(self.section_durations)
                    self.total_estimated = False
                    self.set_busy(False)
                    if self.stream:
                        self.stream.complete = True
                        self.next_section()
                    else:
                        self.play()
        except queue.Empty:
            pass
        if self.player and self.player.poll() is not None:
            code = self.player.returncode
            details = ''
            if self.playback_log:
                self.playback_log.seek(0)
                details = self.playback_log.read()[-2000:]
            if code == 0:
                self.clock.finish()
            continuation = self.playback_continuation
            paused = self.paused
            self.stop_player()
            self.paused = paused
            if code != 0:
                self.playback_failed(details or 'The audio player could not open your output device.')
            elif continuation:
                self.start_file(continuation[0], seek=continuation[1])
                if paused:
                    self.pause()
            elif self.stream:
                self.next_section()
            else:
                self.status.set('Finished')
        if self.preview_player and self.preview_player.poll() is not None:
            code = self.preview_player.returncode
            detail = ''
            if self.preview_log:
                self.preview_log.seek(0)
                detail = self.preview_log.read()[-2000:]
            self.stop_preview()
            self.status.set('Narrator preview finished' if code == 0 else 'Narrator preview failed')
            if code != 0:
                messagebox.showerror('Narrator preview', detail or 'Audio output unavailable')
        playing = self.player is not None and self.player.poll() is None
        for label, enabled in {
            'Back 10s': bool(self.audio) or bool(self.stream and self.stream.parts),
            'Forward 10s': bool(self.audio) or bool(self.stream and self.stream.parts),
            'Pause': (playing or bool(self.stream and self.stream.active)) and not self.paused,
            'Stop': self.busy or playing or self.preview_player is not None or bool(self.stream and self.stream.active and not self.stream.complete),
            'Restart': bool(self.audio) or bool(self.stream and self.stream.parts),
            'Save WAV': bool(self.audio),
            'Open WAV': not self.busy,
            'Play / Resume': (not self.busy or self.stream is not None) and not self.preview_loading,
        }.items():
            self.buttons[label].configure(state='normal' if enabled else 'disabled')
        self.update_follow()
        position, session = self.clock.snapshot()
        total = max(self.total_seconds, position)
        estimate = '~' if self.total_estimated else ''
        total_text = estimate + format_time(total) if total else '—'
        self.timer_text.set(f'{format_time(position)} / {total_text}')
        self.progress.configure(maximum=100, value=min(100, position / total * 100) if total else 0)
        self.root.after(100, self.poll)

    def seek_relative(self, change):
        if self.preview_loading:
            return
        self.stop_preview()
        position = self.clock.snapshot()[0]
        available = sum(self.section_durations) if self.stream else (duration(self.audio) if self.audio else 0)
        if not available:
            self.status.set('Wait for audio before seeking.')
            return
        target = max(0, min(available, position+change))
        was_paused = self.paused
        self.stop_player()
        if self.stream:
            index, within = locate_time(target, self.section_durations)
            self.stream.active = True
            self.stream.cursor = index+1
            if within >= self.section_durations[index]-.001:
                self.clock.start(target, 0)
                self.paused = was_paused
                self.next_section()
            else:
                self.start_file(self.stream.parts[index], seek=within)
                if was_paused:
                    self.pause()
        elif target < available:
            self.start_file(self.audio, seek=target)
            if was_paused:
                self.pause()
        else:
            self.clock.start(target, 0)
            self.status.set('Finished')
        if position+change > available and self.busy:
            self.status.set('Reached the generated audio. Use Read from here to jump further ahead.')

    def request_reading(self, text):
        if not text.strip():
            self.status.set('Select a passage or place the text cursor where you want to start.')
            return
        self.stop()
        if self.busy:
            self.pending_reading = text
            self.status.set('Turning to the selected passage…')
        else:
            self.narrate(text_override=text)

    def read_from_here(self):
        widget = self.reader if self.pages.select() == str(self.reader_page) else self.text
        try:
            start = widget.index('sel.first')
        except tk.TclError:
            start = widget.index('insert')
        if widget is self.reader and not self.follow_ranges:
            self.status.set('Open Source to select a starting passage.')
            return
        self.request_reading(widget.get(start, 'end').strip())

    def jump_heading(self, source_offset):
        self.request_reading(self.text.get('1.0', 'end')[source_offset:].strip())

    def adjacent_heading(self, direction):
        current = self.stream.cursor-1 if self.stream else passage_at(self.clock.snapshot()[0], self.follow_durations)
        current = current+self.playback_passage_offset if current is not None else self.playback_passage_offset-1
        targets = [entry for entry in self.heading_targets if (entry[2] > current if direction > 0 else entry[2] < current)]
        if targets:
            self.jump_heading((targets[0] if direction > 0 else targets[-1])[1])
        else:
            self.status.set('No further detected heading in that direction.')

    def navigation_context(self, event):
        widget = event.widget
        if not widget.tag_ranges('sel'):
            widget.mark_set('insert', f'@{event.x},{event.y}')
        return self.navigation_menu(event)

    def navigation_menu(self, event=None):
        menu = tk.Menu(self.root, tearoff=False, background='#ead7ad', foreground=INK)
        menu.add_command(label='Read from selection / cursor', command=self.read_from_here)
        menu.add_separator()
        menu.add_command(label='Back 10 seconds     Alt+Left', command=lambda: self.seek_relative(-10))
        menu.add_command(label='Forward 10 seconds     Alt+Right', command=lambda: self.seek_relative(10))
        menu.add_separator()
        menu.add_command(label='Previous heading', command=lambda: self.adjacent_heading(-1))
        menu.add_command(label='Next heading', command=lambda: self.adjacent_heading(1))
        entries = headings(self.text.get('1.0', 'end'))
        if entries:
            listing = tk.Menu(menu, tearoff=False)
            for title, offset in entries[:80]:
                listing.add_command(label=title[:75], command=lambda offset=offset: self.jump_heading(offset))
            menu.add_cascade(label='Jump to heading', menu=listing)
        else:
            menu.add_command(label='No explicit headings detected', state='disabled')
        try:
            menu.tk_popup(event.x_root if event else self.navigation_button.winfo_rootx(), event.y_root if event else self.navigation_button.winfo_rooty()+self.navigation_button.winfo_height())
        finally:
            menu.grab_release()
        return 'break'

    def narrator_menu(self):
        menu = tk.Menu(self.root, tearoff=False, background='#ead7ad', foreground=INK)
        menu.add_command(label='Choose your narrator', state='disabled')
        names = {voice['name'] for voice in self.voice_catalog}
        for name in ('default', 'Easton', 'Freya', 'Miles', 'Zoey'):
            if name == 'default' or name in names:
                menu.add_radiobutton(label='The tome’s original voice' if name == 'default' else name,
                                     value=name, variable=self.voice, command=self.select_narrator)
        characters = ('Captain Silas Blackwater', 'Morwenna of the Deep', 'Old Mortlake', 'Azgorath', 'Fennick the Young Elf', 'The Thing Under the Stairs', 'Vyrmathrax the Ancient', 'Grub the Ogre', 'Master Aldric', 'The Snow Queen', 'Sir Bramwell', 'Fairy Godmother')
        fantasy = tk.Menu(menu, tearoff=False)
        for name in characters:
            if name in names:
                fantasy.add_radiobutton(label=name, value=name, variable=self.voice, command=self.select_narrator)
        menu.add_cascade(label='Character voices', menu=fantasy)
        more = tk.Menu(menu, tearoff=False)
        for label, low, high in [('A–F', 'a', 'g'), ('G–N', 'g', 'o'), ('O–Z', 'o', '{')]:
            group = tk.Menu(more, tearoff=False)
            for name in sorted(names):
                if low <= name.lower()[0] < high:
                    group.add_radiobutton(label=name, value=name, variable=self.voice, command=self.select_narrator)
            more.add_cascade(label=label, menu=group)
        menu.add_cascade(label='All English voices', menu=more)
        menu.add_separator()
        menu.add_command(label='Preview selected narrator', command=self.preview_narrator,
                         state='disabled' if self.busy or self.player or self.preview_player else 'normal')
        menu.add_command(label='First use downloads a voice; cached voices work offline', state='disabled')
        try:
            menu.tk_popup(self.narrator_button.winfo_rootx(), self.narrator_button.winfo_rooty()+self.narrator_button.winfo_height())
        finally:
            menu.grab_release()

    def select_narrator(self):
        self.preferences['voice'] = self.voice.get()
        try:
            save_preferences(self.preferences)
            self.status.set('Narrator selected: ' + self.voice.get() + '. Applies to the next narration.')
        except OSError:
            self.status.set('Narrator selected for this session; could not save preference.')

    def stop_preview(self):
        if self.preview_player and self.preview_player.poll() is None:
            self.preview_player.terminate()
            try:
                self.preview_player.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.preview_player.kill()
                self.preview_player.wait()
        self.preview_player = None
        if self.preview_log:
            self.preview_log.close()
            self.preview_log = None

    def preview_narrator(self):
        if self.busy or self.player or self.preview_player:
            self.status.set('Stop playback before previewing a narrator.')
            return
        voice = self.voice.get()
        self.preview_voice_name = voice
        self.preview_loading = True
        self.cancel.clear()
        self.set_busy(True)
        self.status.set('Summoning ' + voice + '… First use may download voice files.')
        def work():
            try:
                directory = Path(tempfile.mkdtemp(prefix='voice-', dir=self.temp.name))
                job = directory / 'job.json'
                job.write_text(json.dumps({'directory': str(directory), 'voice': voice, 'chunks': ['Rest a moment, adventurer. Set down your pack, and let the tome carry the words from here.']}))
                with (directory / 'errors.log').open('w+') as errors:
                    with self.lock:
                        if self.cancel.is_set():
                            return
                        process = subprocess.Popen([sys.executable, '-m', 'runespeak.worker', str(job)], stdout=subprocess.DEVNULL, stderr=errors)
                        self.process = process
                    code = process.wait()
                    if self.cancel.is_set():
                        return
                    if code:
                        errors.seek(0)
                        raise RuntimeError(errors.read()[-2000:] or 'Voice preview failed')
                self.events.put(('voice_preview', directory / '00000.wav'))
            except Exception as exc:
                if not self.cancel.is_set():
                    self.events.put(('load_error', str(exc)))
            finally:
                with self.lock:
                    self.process = None
                self.events.put(('idle', None))
                if self.closed:
                    self.temp.cleanup()
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def prepare_follow(self, pieces):
        self.reader.configure(state='normal')
        self.reader.delete('1.0', 'end')
        self.playback_passage_offset = 0
        self.follow_ranges = []
        self.follow_durations = []
        self.follow_active = None
        self.heading_targets = heading_targets(self.text.get('1.0', 'end'), pieces)
        for piece in pieces:
            if self.follow_ranges:
                self.reader.insert('end', '\n\n')
            start = self.reader.index('end-1c')
            self.reader.insert('end', piece)
            self.follow_ranges.append((start, self.reader.index('end-1c')))
        if not pieces:
            self.reader.insert('end', 'Play a new narration to follow its spoken passages. Saved audio needs its matching JSON section timings for read-along.')
        self.reader.configure(state='disabled')

    def clear_follow(self):
        self.reader.tag_remove('speaking', '1.0', 'end')
        self.follow_active = None

    def update_follow(self):
        if not self.follow_ranges or not self.player:
            if not self.paused:
                self.clear_follow()
            return
        index = self.stream.cursor - 1 if self.stream else passage_at(self.clock.snapshot()[0], self.follow_durations)
        if index is not None:
            index += self.playback_passage_offset
        if index is None or not 0 <= index < len(self.follow_ranges):
            return
        if not self.highlight_enabled.get():
            self.reader.tag_remove('speaking', '1.0', 'end')
        if index == self.follow_active:
            return
        self.follow_active = index
        self.reader.tag_remove('speaking', '1.0', 'end')
        start, end = self.follow_ranges[index]
        if self.highlight_enabled.get():
            self.reader.tag_add('speaking', start, end)
        if self.follow_scroll.get():
            self.reader.yview(start)

    def set_highlight_color(self, color):
        self.highlight_color = color
        red, green, blue = self.reader.winfo_rgb(color)
        luminance = (.2126*red + .7152*green + .0722*blue) / 65535
        self.reader.tag_configure('speaking', background=color, foreground=INK if luminance > .5 else '#fff1cd')

    def highlight_menu(self):
        menu = tk.Menu(self.root, tearoff=False, background='#ead7ad', foreground=INK)
        menu.add_checkbutton(label='Highlight the current passage', variable=self.highlight_enabled, command=self.refresh_follow)
        menu.add_checkbutton(label='Scroll with the reader', variable=self.follow_scroll, command=self.refresh_follow)
        menu.add_separator()
        for name, color in [('Antique gold (default)', '#dcc57c'), ('Sage green', '#c3d1a7'), ('Rose parchment', '#e7bcb0'), ('Moonstone blue', '#bdcedb'), ('Lavender ink', '#cfc0df')]:
            menu.add_command(label=name, background=color, command=lambda color=color: self.set_highlight_color(color))
        menu.add_command(label='Choose a custom color…', command=self.custom_highlight_color)
        menu.add_separator()
        menu.add_command(label='Follows passages; exact word timing is unavailable', state='disabled')
        try:
            menu.tk_popup(self.highlighter_button.winfo_rootx(), self.highlighter_button.winfo_rooty()+self.highlighter_button.winfo_height())
        finally:
            menu.grab_release()

    def refresh_follow(self):
        self.follow_active = None
        self.update_follow()

    def custom_highlight_color(self):
        _, color = colorchooser.askcolor(color=self.highlight_color, title='Choose the tome’s highlight color', parent=self.root)
        if color:
            self.set_highlight_color(color)

    def enqueue_command(self, message):
        done = threading.Event()
        response = {}
        self.events.put(('command', (message, done, response)))
        if not done.wait(3):
            done.set()  # An expired command must not start narration later.
            return {'ok': False, 'error': 'Runespeak is not responding. Close any open dialog and try again.'}
        return response

    def handle_command(self, message):
        action = message['action']
        if action == 'open':
            if self.busy:
                return {'ok': False, 'error': 'Stop the current job before opening another file.'}
            if Path(message['path']).suffix.lower() == '.wav':
                self.load_audio(message['path'])
                self.play()
            else:
                self.load_documents([message['path']])
            return {'ok': True}
        if action in ('read', 'clipboard'):
            if self.busy:
                return {'ok': False, 'error': 'Runespeak is generating or loading text. Stop the current job first.'}
            try:
                text = self.root.clipboard_get() if action == 'clipboard' else message['text']
            except tk.TclError:
                return {'ok': False, 'error': 'Copy highlighted text first with Ctrl+C.'}
            if not text.strip():
                return {'ok': False, 'error': 'Select or copy some text first.'}
            self.text.delete('1.0', 'end')
            self.text.insert('1.0', text)
            self.url.set(message.get('source', ''))
            self.narrate()
        else:
            {'pause': self.pause, 'resume': self.play, 'stop': self.stop, 'restart': self.restart}[action]()
        return {'ok': True}

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.progress.stop()
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        if self.bridge:
            self.bridge.close()
        self.stop()
        if not self.worker or not self.worker.is_alive():
            self.temp.cleanup()
        self.root.destroy()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Runespeak speaking tome')
    parser.add_argument('audio', nargs='?', help='Document or saved WAV narration to open')
    args = parser.parse_args()
    if args.audio:
        path = str(Path(args.audio).expanduser().resolve())
        try:
            result = request({'action': 'open', 'path': path})
        except RuntimeError:
            pass  # No existing app; create its window below.
        else:
            if not result.get('ok'):
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror('Open narration', result.get('error', 'Unable to open WAV'))
                root.destroy()
            return
    root = tk.Tk(className='RunespeakLite')
    app = None
    try:
        app = App(root)
        if args.audio:
            if Path(path).suffix.lower() == '.wav':
                app.load_audio(path)
                root.after(0, app.play)
            else:
                app.load_documents([path])
    except (RuntimeError, OSError, ValueError) as exc:
        messagebox.showerror('Runespeak Lite', str(exc))
        if app:
            app.close()
        else:
            root.destroy()
        return
    root.mainloop()


if __name__ == '__main__':
    main()
