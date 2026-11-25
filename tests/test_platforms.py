"""Cross-platform helpers: folders, the pausable player, Windows playback clipping, bridge."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

try:  # Imported before sys.modules is patched in the stream tests.
    import numpy  # noqa: F401
except ImportError:
    numpy = None

from runespeak import platforms
from runespeak.platforms import AudioPlayer
from runespeak.winplay import clip

# A stand-in player: records its arguments, then plays (sleeps) until terminated.
FAKE_PLAYER = '''
import sys, time, json, pathlib
log = pathlib.Path(sys.argv[1])
log.write_text(log.read_text() + json.dumps(sys.argv[2:]) + "\\n" if log.exists() else json.dumps(sys.argv[2:]) + "\\n")
time.sleep(float(__import__("os").environ.get("FAKE_SECONDS", "30")))
'''


def write_wav(path, seconds=1.0, rate=8000):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x01\x00' * int(seconds * rate))


class FolderTests(unittest.TestCase):
    def test_xdg_config_wins_everywhere(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/tmp/xdg'}):
            for name in ('linux', 'darwin', 'win32'):
                with patch.object(platforms.sys, 'platform', name):
                    self.assertEqual(platforms.config_dir(), Path('/tmp/xdg/runespeak-lite'))

    def test_native_folders(self):
        env = {key: value for key, value in os.environ.items() if key not in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME')}
        with patch.dict(os.environ, env, clear=True), patch.object(platforms.Path, 'home', return_value=Path('/home/alice')):
            with patch.object(platforms.sys, 'platform', 'darwin'):
                self.assertEqual(platforms.config_dir(), Path('/home/alice/Library/Application Support/runespeak-lite'))
                self.assertEqual(platforms.state_dir(), Path('/home/alice/Library/Logs/runespeak-lite'))
            with patch.object(platforms.sys, 'platform', 'linux'):
                self.assertEqual(platforms.config_dir(), Path('/home/alice/.config/runespeak-lite'))
            with patch.object(platforms.sys, 'platform', 'win32'), patch.dict(os.environ, {'APPDATA': r'C:\Users\alice\AppData\Roaming'}):
                self.assertEqual(platforms.config_dir(), Path(r'C:\Users\alice\AppData\Roaming') / 'runespeak-lite')

    def test_player_choice(self):
        with patch.object(platforms.sys, 'platform', 'darwin'), patch.object(platforms.shutil, 'which', return_value='/usr/bin/afplay'):
            self.assertEqual(platforms.player_command(prefer_stream=False), ['afplay'])
        with patch.object(platforms.sys, 'platform', 'win32'):
            self.assertEqual(platforms.player_command(prefer_stream=False)[1:], ['-m', 'runespeak.winplay'])
        with patch.object(platforms, 'stream_available', return_value=True):
            self.assertEqual(tuple(platforms.player_command()), platforms.STREAM)
        with patch.object(platforms.sys, 'platform', 'linux'), patch.object(platforms.shutil, 'which', side_effect=lambda name: name == 'paplay' and '/usr/bin/paplay'):
            self.assertEqual(platforms.player_command(), ['paplay'])
        with patch.object(platforms.sys, 'platform', 'linux'), patch.object(platforms.shutil, 'which', return_value=None):
            self.assertIsNone(platforms.player_command())


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.script = root / 'player.py'
        self.script.write_text(FAKE_PLAYER)
        self.log = root / 'calls.jsonl'
        self.audio = root / 'a.wav'
        write_wav(self.audio)
        self.command = [sys.executable, str(self.script), str(self.log)]

    def tearDown(self):
        self.folder.cleanup()

    def calls(self):
        deadline = time.monotonic() + 5
        while not self.log.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    @unittest.skipIf(sys.platform == 'win32', 'POSIX signals')
    def test_posix_pause_keeps_one_process(self):
        player = AudioPlayer(self.audio, command=self.command)
        pid = player.pid
        player.pause()
        self.assertIsNone(player.poll())
        player.resume()
        self.assertEqual(player.pid, pid)
        player.terminate()
        player.wait(timeout=5)
        self.assertIsNotNone(player.poll())

    def test_windows_style_pause_relaunches_at_offset(self):
        with patch.object(platforms.sys, 'platform', 'win32'):
            player = AudioPlayer(self.audio, command=self.command)
            self.assertFalse(player.signals)
            self.calls()
            time.sleep(.3)
            player.pause()
            self.assertIsNone(player.poll(), 'paused counts as still playing')
            player.resume()
            deadline = time.monotonic() + 5
            while len(self.calls()) < 2 and time.monotonic() < deadline:
                time.sleep(.02)
            first, second = self.calls()
            self.assertEqual(first, [str(self.audio)])
            self.assertEqual(second[:2], [str(self.audio), '--start'])
            self.assertGreaterEqual(float(second[2]), .25)
            player.terminate()
            player.wait(timeout=5)

    def test_finished_player_reports_exit(self):
        with patch.dict(os.environ, {'FAKE_SECONDS': '0'}):
            player = AudioPlayer(self.audio, command=self.command)
            self.assertEqual(player.wait(timeout=10), 0)
            self.assertEqual(player.poll(), 0)

    def test_missing_player_is_an_error(self):
        with patch.object(platforms, 'player_command', return_value=None):
            with self.assertRaises(OSError):
                AudioPlayer(self.audio)


class FakeOutputStream:
    """Stands in for sounddevice.OutputStream: pulls blocks from the callback on a thread."""
    def __init__(self, samplerate, channels, dtype, latency, callback, blocksize=0):
        import numpy
        self.callback, self.channels, self.numpy = callback, channels, numpy
        self.samplerate, self.blocksize = samplerate, blocksize
        self.active = False
        self.blocks = 0
        self.played = []

    def start(self):
        self.active = True
        def pump():
            while self.active:
                size = self.blocksize or 800
                block = self.numpy.zeros((size, self.channels), dtype='int16')
                self.callback(block, size, None, None)
                self.played.append(block.copy())
                self.blocks += 1
                # Four times real time: fast tests, but a realistic pace for the converter.
                time.sleep(size / self.samplerate / 4 if self.blocksize else .002)
        threading.Thread(target=pump, daemon=True).start()

    def stop(self):
        self.active = False

    close = stop


@unittest.skipUnless(numpy is not None, 'needs numpy')
class StreamPlayerTests(unittest.TestCase):
    def setUp(self):
        import types
        fake = types.SimpleNamespace(OutputStream=FakeOutputStream, query_devices=lambda kind=None: {'default_samplerate': 8000})
        self.patch = patch.dict(sys.modules, {'sounddevice': fake})
        self.patch.start()
        platforms.StreamOutput._shared = None
        self.folder = tempfile.TemporaryDirectory()
        self.audio = Path(self.folder.name) / 'a.wav'
        write_wav(self.audio, seconds=0.5)

    def tearDown(self):
        if platforms.StreamOutput._shared:
            platforms.StreamOutput._shared.close()
        platforms.StreamOutput._shared = None
        self.patch.stop()
        self.folder.cleanup()

    def test_passages_share_one_stream_and_finish(self):
        first = AudioPlayer(self.audio, command=list(platforms.STREAM))
        self.assertIsInstance(first, platforms.StreamPlayer)
        stream = platforms.StreamOutput._shared
        self.assertEqual(first.wait(timeout=5), 0)
        second = AudioPlayer(self.audio, command=list(platforms.STREAM))
        self.assertIs(platforms.StreamOutput._shared, stream, 'no new stream per passage')
        self.assertEqual(second.wait(timeout=5), 0)

    def test_pause_holds_position_and_terminate_stops(self):
        player = AudioPlayer(self.audio, command=list(platforms.STREAM))
        player.pause()
        held = player.job.position
        time.sleep(.1)
        self.assertEqual(player.job.position, held)
        self.assertIsNone(player.poll())
        player.resume()
        player.terminate()
        self.assertEqual(player.poll(), -15)

    def use_device_rate(self, rate):
        sys.modules['sounddevice'].query_devices = lambda kind=None: {'default_samplerate': rate}

    def test_stream_runs_at_device_rate_with_large_blocks(self):
        # Opening at the model rate let Core Audio pull 14-frame blocks, so the Python
        # callback ran ~1,700 times a second and starved whenever the app was busy.
        self.use_device_rate(44100)
        write_wav(self.audio, seconds=0.2, rate=24000)
        player = AudioPlayer(self.audio, command=list(platforms.STREAM))
        stream = platforms.StreamOutput._shared.stream
        self.assertEqual(stream.samplerate, 44100)
        self.assertGreaterEqual(stream.blocksize / stream.samplerate, 0.03)
        self.assertEqual(player.wait(timeout=5), 0)

    def test_every_sample_reaches_the_device_once_in_order(self):
        self.use_device_rate(24000)
        ramp = (numpy.arange(5000) % 2000 - 1000).astype('<i2')
        with wave.open(str(self.audio), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
            audio.writeframes(ramp.tobytes())
        player = AudioPlayer(self.audio, command=list(platforms.STREAM))
        self.assertEqual(player.wait(timeout=5), 0)
        played = numpy.concatenate(platforms.StreamOutput._shared.stream.played)[:, 0]
        start = int(numpy.flatnonzero(played)[0])
        numpy.testing.assert_array_equal(played[start:start + len(ramp)][1:], ramp[1:])

    def test_resampled_passage_arrives_whole_while_converting_ahead(self):
        # Long recordings (replays, seeks) are converted in the background; playback must
        # still receive exactly the band-limited conversion, with nothing skipped.
        self.use_device_rate(44100)
        source = (numpy.sin(numpy.arange(24000 * 3) * 0.05) * 12000).astype('<i2')
        with wave.open(str(self.audio), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
            audio.writeframes(source.tobytes())
        player = AudioPlayer(self.audio, command=list(platforms.STREAM))
        self.assertLess(player.job.ready, len(player.job.frames), 'only the start is converted up front')
        self.assertEqual(player.wait(timeout=20), 0)
        expected = platforms.resample(source[:, None], 24000, 44100)[:, 0]
        numpy.testing.assert_array_equal(player.job.frames[:, 0], expected)
        played = numpy.concatenate(platforms.StreamOutput._shared.stream.played)[:, 0]
        start = int(numpy.flatnonzero(played)[0]) - int(numpy.flatnonzero(expected)[0])
        numpy.testing.assert_array_equal(played[start:start + len(expected)], expected)
        self.assertEqual(platforms.StreamOutput._shared.underflows, 0)

    def test_underflows_are_counted(self):
        self.use_device_rate(24000)
        output = platforms.StreamOutput(24000, 1)
        block = numpy.zeros((10, 1), dtype='int16')
        output._callback(block, 10, None, type('S', (), {'output_underflow': True})())
        self.assertEqual(output.underflows, 1)
        output.close()


@unittest.skipUnless(numpy is not None, 'needs numpy')
class ResampleTests(unittest.TestCase):
    def tone(self, rate, hertz=1000.0, seconds=0.5, level=0.5):
        t = numpy.arange(int(rate * seconds)) / rate
        return level * numpy.sin(2 * numpy.pi * hertz * t)

    def check(self, source, target):
        wave_in = numpy.rint(self.tone(source) * 32767).astype('int16')[:, None]
        out = platforms.resample(wave_in, source, target)
        self.assertEqual(len(out), len(wave_in) * target // source)
        ideal = self.tone(target)[:len(out)] * 32767
        middle = slice(target // 20, -target // 20)  # Ignore the start and end ramps.
        error = out[middle, 0] - ideal[middle]
        snr = 10 * numpy.log10(numpy.mean(ideal[middle] ** 2) / numpy.mean(error ** 2))
        self.assertGreater(snr, 60, f'{source}->{target} Hz resampling is noisy ({snr:.1f} dB)')

    def test_model_rate_to_common_device_rates_is_clean(self):
        self.check(24000, 44100)
        self.check(24000, 48000)
        self.check(24000, 96000)

    def test_same_rate_is_untouched(self):
        frames = numpy.arange(10, dtype='int16')[:, None]
        self.assertIs(platforms.resample(frames, 24000, 24000), frames)

    def test_full_scale_does_not_wrap_around(self):
        square = numpy.where(numpy.arange(2400) % 40 < 20, 32767, -32768).astype('int16')[:, None]
        out = platforms.resample(square, 24000, 44100)
        self.assertEqual(out.dtype, numpy.int16)
        # Overshoot is clipped, never wrapped to the opposite sign.
        self.assertFalse(numpy.any((out[1:-1, 0].astype(int) * out[:-2, 0].astype(int) < -2 ** 29)))


class WindowsClipTests(unittest.TestCase):
    def test_clip_starts_at_offset_and_keeps_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'a.wav'
            write_wav(path, seconds=2.0)
            data = clip(path, 0.5)
            target = Path(folder) / 'b.wav'
            target.write_bytes(data)
            with wave.open(str(target), 'rb') as audio:
                self.assertEqual(audio.getframerate(), 8000)
                self.assertAlmostEqual(audio.getnframes() / 8000, 1.5, places=2)
            self.assertLessEqual(len(clip(path, 99)), 100)


class LoopbackBridgeTests(unittest.TestCase):
    """The Windows command channel: loopback only, random port, per-session token."""

    def test_round_trip_and_token_required(self):
        import socket
        from runespeak import bridge
        with tempfile.TemporaryDirectory() as folder, \
                patch.dict(os.environ, {'RUNESPEAK_RUNTIME_DIR': folder}), \
                patch.object(bridge, 'is_windows', return_value=True):
            received = []
            server = bridge.CommandServer(lambda message: received.append(message) or {'ok': True})
            try:
                endpoint = json.loads(bridge.endpoint_path().read_text())
                self.assertEqual(server.server.server_address[0], '127.0.0.1')
                self.assertEqual(bridge.request({'action': 'read', 'text': 'Hello there.'}), {'ok': True})
                self.assertEqual(received, [{'action': 'read', 'text': 'Hello there.'}])
                with socket.create_connection(('127.0.0.1', endpoint['port']), timeout=5) as client:
                    client.sendall(json.dumps({'action': 'stop', 'token': 'wrong'}).encode() + b'\n')
                    reply = json.loads(client.makefile('rb').readline())
                self.assertFalse(reply['ok'])
                self.assertEqual(len(received), 1)
                with self.assertRaises(RuntimeError):
                    bridge.CommandServer(lambda message: {'ok': True})
            finally:
                server.close()
            with self.assertRaises(RuntimeError):
                bridge.request({'action': 'stop'})


if __name__ == '__main__':
    unittest.main()
