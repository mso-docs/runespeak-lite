"""Isolated synthesis process: terminating it cancels native model work."""
import json
import os
import platform
import sys
import time
from pathlib import Path


def synthesis_threads(cpus=None):
    """Threads for the model. Synthesis already runs faster than speech with four, and
    using every core starves the system's audio and Bluetooth services, so playback
    crackles. ``RUNESPEAK_THREADS`` overrides the choice."""
    chosen = os.environ.get('RUNESPEAK_THREADS', '')
    if chosen.isdigit() and int(chosen) > 0:
        return int(chosen)
    cpus = cpus or os.cpu_count() or 4
    return max(1, min(3, cpus // 2))


def weight_format(machine=None):
    """Full-precision weights on Intel/AMD: there the 8-bit path costs about twice the
    CPU per second of speech (measured on an i7-9750H). ``RUNESPEAK_QUANT`` overrides."""
    chosen = os.environ.get('RUNESPEAK_QUANT', '').strip()
    if chosen:
        return chosen
    machine = (machine or platform.machine()).lower()
    return 'f32' if machine in ('x86_64', 'amd64', 'x64', 'i686', 'x86') else 'q8'


def lower_priority():
    """Let playback and the rest of the system win whenever the CPU is busy."""
    try:
        if sys.platform == 'win32':
            import ctypes
            below_normal = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), below_normal)
        else:
            os.nice(10)
    except Exception:
        pass


class LocalEngine:
    """Phonon on this device, loaded only when first needed."""

    def __init__(self):
        self.tts = None

    def save(self, path, text, voice=None):
        if self.tts is None:
            import ptts
            ptts.set_num_threads(synthesis_threads())
            self.tts = ptts.TTS('hf://gradium/phonon-31m-en', lang='en', quant=weight_format())
        return self.tts.save(path, text, voice=voice)


def notice(message):
    print(f'notice {message}', flush=True)


def main(reprobe_seconds=None):
    lower_priority()
    job = json.loads(Path(sys.argv[1]).read_text())
    voice = None if job.get('voice', 'default') == 'default' else job['voice']
    local = LocalEngine()      # Throttled: at most three threads, low priority.
    engine = local
    server = None
    reprober = None
    settings = job.get('engine') or {}
    if settings.get('kind') == 'server' and settings.get('url'):
        from . import backends
        try:
            server = backends.ServerEngine(settings['url'], os.environ.pop(backends.TOKEN_ENV, ''),
                                           float(settings.get('timeout', backends.DEFAULT_TIMEOUT)))
        except ValueError:
            server = None
        if server is None:
            notice(backends.fallback_notice())
        else:
            try:
                backends.cached_health(server)      # Before the first passage.
                engine = server
            except backends.ServerError:
                notice(backends.fallback_notice())
                reprober = backends.Reprober(server, reprobe_seconds or backends.REPROBE_SECONDS)
    try:
        for index, text in enumerate(job['chunks']):
            path = str(Path(job['directory']) / f'{index:05d}.wav')
            if engine is local and reprober is not None and reprober.ready.is_set():
                engine = server                     # Back at a passage boundary.
                reprober = None
                notice(backends.BACK_NOTICE)
            started = time.monotonic()
            try:
                seconds = engine.save(path, text, voice=voice)
            except Exception:
                if engine is local:
                    raise
                # The server dropped: make this same passage here, so nothing is skipped
                # or repeated, and keep checking for the server in the background.
                notice(backends.fallback_notice())
                engine = local
                reprober = backends.Reprober(server, reprobe_seconds or backends.REPROBE_SECONDS)
                started = time.monotonic()
                seconds = engine.save(path, text, voice=voice)
            print(f'timing {index} {time.monotonic() - started:.3f} {seconds or 0:.3f} '
                  f'{"server" if engine is server else "local"}', flush=True)
            print(index + 1, flush=True)
    finally:
        if reprober is not None:
            reprober.stop()


if __name__ == '__main__':
    main()
