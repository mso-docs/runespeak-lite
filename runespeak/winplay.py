"""Windows WAV player helper: ``python -m runespeak.winplay FILE [--start SECONDS]``.

Plays synchronously with the standard-library winsound module, so ending this process
ends playback. ``--start`` lets the app resume a paused narration where it left off.
"""
import argparse
import io
import sys
import wave


def clip(path, start=0.0):
    """Return WAV bytes for ``path`` beginning ``start`` seconds in."""
    with wave.open(str(path), 'rb') as source:
        rate = source.getframerate()
        first = min(source.getnframes(), max(0, int(start * rate)))
        source.setpos(first)
        frames = source.readframes(source.getnframes() - first)
        params = source.getparams()
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setparams(params)
        output.writeframes(frames)
    return buffer.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Play a WAV file (Windows helper for Runespeak Lite)')
    parser.add_argument('path')
    parser.add_argument('--start', type=float, default=0.0)
    options = parser.parse_args(argv)
    try:
        import winsound
    except ImportError:
        print('winsound is only available on Windows', file=sys.stderr)
        return 2
    data = clip(options.path, options.start)
    if len(data) <= 44:
        return 0
    try:
        winsound.PlaySound(data, winsound.SND_MEMORY)
    except RuntimeError as exc:
        print(f'Windows could not play this audio: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
