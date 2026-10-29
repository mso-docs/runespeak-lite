"""Playback timing independent of UI, with a monotonic, pause-aware clock."""
import time
import wave


def duration(path):
    try:
        with wave.open(str(path), 'rb') as audio:
            return audio.getnframes() / audio.getframerate()
    except (OSError, EOFError, wave.Error):
        return None


def format_time(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02}:{seconds:02}' if hours else f'{minutes}:{seconds:02}'


class ListeningClock:
    def __init__(self, now=time.monotonic):
        self.now = now
        self.position = 0.0
        self.session = 0.0
        self.remaining = 0.0
        self.started = None

    def delta(self):
        return min(self.remaining, max(0, self.now() - self.started)) if self.started is not None else 0.0

    def snapshot(self):
        elapsed = self.delta()
        return self.position + elapsed, self.session + elapsed

    def pause(self):
        elapsed = self.delta()
        self.position += elapsed
        self.session += elapsed
        self.remaining -= elapsed
        self.started = None

    def resume(self):
        if self.started is None and self.remaining > 0:
            self.started = self.now()

    def start(self, offset, length):
        self.pause()
        self.position = offset
        self.remaining = max(0, length)
        self.resume()

    def finish(self):
        self.pause()
        # Snap the playhead to the WAV boundary after successful playback.
        self.position += self.remaining
        self.remaining = 0

    def reset(self):
        self.pause()
        self.position = 0
        self.remaining = 0
