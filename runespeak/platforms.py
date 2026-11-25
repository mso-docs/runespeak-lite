"""Operating-system differences in one place: folders, audio playback, child processes.

Linux keeps its original behavior (XDG folders, pw-play/paplay/aplay). On macOS and
Windows, sections play through one always-open PortAudio output stream (``sounddevice``),
so consecutive passages follow each other without the second-long start-up gap of a new
player process per passage. Without PortAudio, macOS falls back to ``afplay`` and Windows to
the standard-library ``winsound`` in a small helper process (Windows has no SIGSTOP/SIGCONT).
"""
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave

APP_DIR = 'runespeak-lite'
LINUX_PLAYERS = ('pw-play', 'paplay', 'aplay')


def is_windows():
    return sys.platform == 'win32'


def is_macos():
    return sys.platform == 'darwin'


def config_dir():
    """Preferences folder. XDG_CONFIG_HOME always wins so existing Linux setups keep working."""
    if os.environ.get('XDG_CONFIG_HOME'):
        return Path(os.environ['XDG_CONFIG_HOME']) / APP_DIR
    if is_windows():
        return Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / APP_DIR
    if is_macos():
        return Path.home() / 'Library' / 'Application Support' / APP_DIR
    return Path.home() / '.config' / APP_DIR


def state_dir():
    """Log folder for launcher and app errors."""
    if os.environ.get('XDG_STATE_HOME'):
        return Path(os.environ['XDG_STATE_HOME']) / APP_DIR
    if is_windows():
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / APP_DIR / 'logs'
    if is_macos():
        return Path.home() / 'Library' / 'Logs' / APP_DIR
    return Path.home() / '.local' / 'state' / APP_DIR


def hidden_window_flags():
    """creationflags that keep helper processes from flashing a console window on Windows."""
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if is_windows() else 0


def python_command(module, *args):
    """Run one of our modules with this interpreter (works in a checkout and in the bundles)."""
    return [sys.executable, '-m', module, *map(str, args)]


def kill_tree(process):
    """Forcefully stop a helper started with :func:`start_isolated` and its children."""
    if process.poll() is not None:
        return
    if is_windows():
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=hidden_window_flags(), check=False)
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def start_isolated(args, **kwargs):
    """Start a converter (pdftotext, tesseract, LibreOffice) in its own process group."""
    if is_windows():
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | hidden_window_flags() | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    return subprocess.Popen(args, **kwargs)


STREAM = ('<portaudio-stream>',)
_stream_ok = None


def stream_available():
    """True when a PortAudio output device can be used (macOS and Windows bundles)."""
    global _stream_ok
    if os.environ.get('RUNESPEAK_AUDIO', '').lower() == 'process' or not (is_macos() or is_windows()):
        return False
    if _stream_ok is None:
        try:
            import sounddevice
            sounddevice.query_devices(kind='output')
            _stream_ok = True
        except Exception:
            _stream_ok = False
    return _stream_ok


def player_command(prefer_stream=True):
    """WAV player for this system: the shared stream, a command line, or None."""
    if prefer_stream and stream_available():
        return list(STREAM)
    if is_windows():
        return python_command('runespeak.winplay')
    if is_macos():
        return ['afplay'] if shutil.which('afplay') else None
    found = next((name for name in LINUX_PLAYERS if shutil.which(name)), None)
    return [found] if found else None


def missing_player_message():
    if is_macos():
        return 'macOS could not find afplay (/usr/bin/afplay).'
    if is_windows():
        return 'Windows audio playback is unavailable in this Python (winsound is missing).'
    return 'Install a PipeWire, PulseAudio, or ALSA command-line player (pw-play, paplay or aplay).'


class _Job:
    """One passage on the shared stream. ``frames`` is filled up to ``ready`` while a
    helper thread resamples ahead of the playback position."""
    def __init__(self, frames, ready=None):
        self.frames = frames
        self.ready = len(frames) if ready is None else ready
        self.position = 0
        self.paused = False
        self.done = False


RESAMPLE_TAPS = 48       # Sinc taps per output sample; keeps aliasing far below hearing.
BLOCK_SECONDS = 0.045    # Audio handed over per callback.
FIRST_SECONDS = 0.25     # Resampled before a passage starts; the rest follows in the background.


class Resampler:
    """Band-limited (Kaiser-windowed sinc, polyphase) resampling of int16 frames.

    Linear interpolation, or a driver converter fed tiny blocks, adds the faint grit that
    sounds like crackle on speech; this keeps the voice clean. ``block(first, last)``
    returns output frames ``first:last`` so long recordings can be converted piecewise."""

    def __init__(self, frames, source_rate, target_rate, taps=RESAMPLE_TAPS):
        import numpy
        from fractions import Fraction
        self.numpy = numpy
        ratio = Fraction(target_rate, source_rate).limit_denominator(1000)
        self.up, self.down = ratio.numerator, ratio.denominator
        cutoff = min(1.0, self.up / self.down) * 0.94   # Fraction of the source Nyquist kept.
        self.half = taps // 2
        self.offsets = numpy.arange(-self.half + 1, self.half + 1)
        distance = self.offsets[None, :] - (numpy.arange(self.up) / self.up)[:, None]
        table = cutoff * numpy.sinc(cutoff * distance) * numpy.kaiser(taps, 8.6)[None, :]
        table /= table.sum(axis=1, keepdims=True)        # Unity gain in every phase.
        self.table = table.astype(numpy.float32)
        pad = numpy.zeros((self.half + 1, frames.shape[1]), numpy.float32)
        self.padded = numpy.concatenate([pad[:-1], frames.astype(numpy.float32), pad])
        self.length = len(frames) * self.up // self.down
        self.channels = frames.shape[1]

    def block(self, first, last):
        numpy = self.numpy
        last = min(last, self.length)
        base, phase = divmod(numpy.arange(first, last) * self.down, self.up)
        out = numpy.zeros((last - first, self.channels), dtype=numpy.float32)
        for index, offset in enumerate(self.offsets):    # One vectorized pass per tap.
            out += self.padded[base + self.half + offset] * self.table[phase, index][:, None]
        return numpy.clip(numpy.rint(out), -32768, 32767).astype(numpy.int16)


def resample(frames, source_rate, target_rate, taps=RESAMPLE_TAPS):
    if source_rate == target_rate or not len(frames):
        return frames
    converter = Resampler(frames, source_rate, target_rate, taps)
    return converter.block(0, converter.length)


class StreamOutput:
    """One always-open output stream. Passages are handed over back to back, so the only
    gap between them is the app's 0.1 s poll, which the device buffer covers.

    The stream runs at the output device's own rate with a fixed ~45 ms block. Opening it
    at the model's 24 kHz made Core Audio's converter ask for 14 frames at a time, about
    1,700 Python callbacks a second; whenever the app's main thread held the interpreter
    lock those callbacks ran late and the voice crackled."""
    _shared = None
    _guard = threading.Lock()

    @classmethod
    def shared(cls, rate, channels):
        with cls._guard:
            current = cls._shared
            if current is None or (current.source_rate, current.channels) != (rate, channels) or not current.alive():
                if current is not None:
                    current.close()
                cls._shared = cls(rate, channels)
            return cls._shared

    def __init__(self, rate, channels):
        import sounddevice
        self.source_rate = rate
        self.channels = channels
        self.lock = threading.Lock()
        self.job = None
        self.underflows = 0
        try:
            device_rate = int(sounddevice.query_devices(kind='output')['default_samplerate'])
        except Exception:
            device_rate = 0
        self.rate = device_rate if device_rate >= 8000 else rate
        self.blocksize = 1 << max(8, round(math.log2(self.rate * BLOCK_SECONDS)))
        self.stream = sounddevice.OutputStream(samplerate=self.rate, channels=channels, dtype='int16',
                                               latency='high', blocksize=self.blocksize,
                                               callback=self._callback)
        self.stream.start()

    def alive(self):
        return self.stream.active

    def _callback(self, output, frames, time_info, status):
        if status and getattr(status, 'output_underflow', False):
            self.underflows += 1
        with self.lock:
            job = self.job
            if job is None or job.paused or job.done:
                output.fill(0)
                return
            piece = job.frames[job.position:min(job.position + frames, job.ready)]
            count = len(piece)
            output[:count] = piece
            output[count:] = 0
            if count < frames and job.ready < len(job.frames):
                self.underflows += 1                     # The converter fell behind.
            job.position += count
            if job.position >= len(job.frames):
                job.done = True

    def play(self, frames):
        if self.rate == self.source_rate or not len(frames):
            job = _Job(frames)
        else:
            import numpy
            converter = Resampler(frames, self.source_rate, self.rate)
            job = _Job(numpy.zeros((converter.length, self.channels), dtype=numpy.int16), 0)
            step = int(self.rate * FIRST_SECONDS)
            job.frames[:step] = converter.block(0, step)
            job.ready = min(step, converter.length)
            if job.ready < converter.length:
                threading.Thread(target=self._convert, args=(job, converter, step), daemon=True).start()
        with self.lock:
            if self.job is not None:
                self.job.done = True
            self.job = job
        return job

    def _convert(self, job, converter, first):
        step = self.rate                                  # One second per pass.
        while first < converter.length and not job.done:
            last = min(first + step, converter.length)
            job.frames[first:last] = converter.block(first, last)
            job.ready = last
            first = last

    def stop(self, job):
        with self.lock:
            job.done = True
            if self.job is job:
                self.job = None

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


def read_frames(path):
    import numpy
    with wave.open(str(path), 'rb') as audio:
        if audio.getsampwidth() != 2:
            raise ValueError('Only 16-bit WAV narration can be played.')
        channels, rate = audio.getnchannels(), audio.getframerate()
        data = numpy.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2')
    return data.reshape(-1, channels), rate, channels


class StreamPlayer:
    """Popen-like handle for one passage on the shared stream."""
    pid = None

    def __init__(self, path):
        frames, rate, channels = read_frames(path)
        self.output = StreamOutput.shared(rate, channels)
        self.returncode = None
        self.job = self.output.play(frames)
        self.paused = False

    def poll(self):
        if self.returncode is None and self.job.done:
            self.returncode = 0
        return self.returncode

    def pause(self):
        self.paused = self.job.paused = True

    def resume(self):
        self.paused = self.job.paused = False

    def terminate(self):
        if self.returncode is None:
            self.output.stop(self.job)
            self.returncode = -15

    kill = terminate

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() > deadline:
                raise subprocess.TimeoutExpired('audio stream', timeout)
            time.sleep(.02)
        return self.returncode


class AudioPlayer:
    """A Popen-like handle that plays one WAV file and can pause and resume it.

    ``poll()`` stays ``None`` while the file is playing *or paused*, matching how the app
    used a bare player process before. POSIX systems pause by stopping the player process.
    Windows cannot stop a process, so pausing ends the helper and resuming starts a new
    one at the remembered position.
    """

    def __new__(cls, path, stderr=None, command=None):
        chosen = command or player_command()
        if chosen and tuple(chosen) == STREAM:
            try:
                return StreamPlayer(path)
            except Exception:  # No usable device: fall back to a player process.
                command = player_command(prefer_stream=False)
        player = super().__new__(cls)
        player._command = command if command and tuple(command) != STREAM else None
        return player

    def __init__(self, path, stderr=None, command=None):
        if isinstance(self, StreamPlayer):
            return
        command = getattr(self, '_command', None) or command
        self.path = Path(path)
        self.stderr = stderr
        self.command = command or player_command()
        if not self.command:
            raise OSError(missing_player_message())
        self.signals = not is_windows()
        self.offset = 0.0
        self.started = None
        self.paused = False
        self.returncode = None
        self.process = None
        self._launch()

    @property
    def pid(self):
        return self.process.pid if self.process else None

    def _launch(self):
        args = list(self.command) + [str(self.path)]
        if not self.signals and self.offset > 0:
            args += ['--start', f'{self.offset:.3f}']
        self.process = subprocess.Popen(args, stderr=self.stderr, creationflags=hidden_window_flags())
        self.started = time.monotonic()

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self.paused and not self.signals:
            return None
        code = self.process.poll()
        if code is not None:
            self.returncode = code
        return code

    def pause(self):
        if self.paused or self.poll() is not None:
            return
        self.paused = True
        if self.signals:
            self.process.send_signal(signal.SIGSTOP)
            return
        self.offset += time.monotonic() - self.started
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def resume(self):
        if not self.paused:
            return
        self.paused = False
        if self.signals:
            self.process.send_signal(signal.SIGCONT)
        else:
            self._launch()

    def terminate(self):
        if self.signals and self.paused and self.process.poll() is None:
            self.process.send_signal(signal.SIGCONT)
        self.paused = False
        if self.process.poll() is None:
            self.process.terminate()

    def kill(self):
        self.paused = False
        if self.process.poll() is None:
            self.process.kill()

    def wait(self, timeout=None):
        code = self.process.wait(timeout=timeout)
        if self.returncode is None:
            self.returncode = code
        return self.returncode

