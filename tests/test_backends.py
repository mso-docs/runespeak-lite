"""Speech engine selection, token storage, the server client and the reference server,
all against a mocked model (no network beyond this computer, no real synthesis)."""
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from runespeak import backends, worker

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'dummy-token'


def load_server_module():
    spec = importlib.util.spec_from_file_location('runespeak_server', ROOT / 'scripts' / 'runespeak-server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tone_wav(seconds=0.5, rate=24000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(rate)
        audio.writeframes(b'\x10\x00' * int(seconds * rate))
    return buffer.getvalue()


class FakeSpeech:
    """Stands in for the server's loaded model."""
    rate = 24000
    voices = ['default', 'narrator']

    def __init__(self):
        self.calls = []

    def wav(self, text, voice=None):
        self.calls.append((text, voice))
        return tone_wav(min(2.0, 0.02 * len(text))), 0.125


class MockServer:
    def __init__(self, token=TOKEN):
        self.module = load_server_module()
        self.speech = FakeSpeech()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), self.module.make_handler(self.speech, token))
        self.httpd.RequestHandlerClass.log_message = lambda *args: None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_port_url():
    import socket
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return f'http://127.0.0.1:{probe.getsockname()[1]}'


class SettingsTests(unittest.TestCase):
    def test_defaults_to_this_device(self):
        self.assertEqual(backends.engine_settings({})['kind'], backends.LOCAL)
        self.assertEqual(backends.engine_settings({'engine': 'junk'})['kind'], backends.LOCAL)
        # A server without an address cannot be used.
        self.assertEqual(backends.engine_settings({'engine': {'kind': 'server', 'url': ''}})['kind'], backends.LOCAL)

    def test_server_settings_and_timeout_bounds(self):
        settings = backends.engine_settings({'engine': {'kind': 'server', 'url': ' http://h:1 ', 'timeout': 9999}})
        self.assertEqual(settings, {'kind': 'server', 'url': 'http://h:1', 'timeout': 300.0})

    def test_url_normalization(self):
        self.assertEqual(backends.normalize_url('192.0.2.10:8790/'), 'http://192.0.2.10:8790')
        self.assertEqual(backends.normalize_url('https://speech.example'), 'https://speech.example')
        for bad in ('', 'ftp://x', 'http://'):
            with self.assertRaises(ValueError):
                backends.normalize_url(bad)


class TokenStorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.folder.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def test_private_file_without_keyring(self):
        with patch.object(backends, '_keyring', return_value=None):
            self.assertEqual(backends.save_token(TOKEN), 'file')
            path = backends.token_path()
            if sys.platform != 'win32':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(backends.load_token(), TOKEN)
            backends.save_token('')
            self.assertFalse(path.exists())
            self.assertEqual(backends.load_token(), '')

    def test_keychain_when_available(self):
        store = {}
        ring = types.SimpleNamespace(
            set_password=lambda s, u, p: store.__setitem__((s, u), p),
            get_password=lambda s, u: store.get((s, u)),
            delete_password=lambda s, u: store.pop((s, u)))
        with patch.object(backends, '_keyring', return_value=ring):
            self.assertEqual(backends.save_token(TOKEN), 'keychain')
            self.assertFalse(backends.token_path().exists(), 'no plain-text copy')
            self.assertEqual(backends.load_token(), TOKEN)
            backends.save_token('')
            self.assertEqual(store, {})


class ClientAndServerTests(unittest.TestCase):
    def setUp(self):
        self.server = MockServer()

    def tearDown(self):
        self.server.close()

    def test_health_and_synthesis(self):
        engine = backends.ServerEngine(self.server.url, TOKEN, 5)
        self.assertEqual(engine.health()['sample_rate'], 24000)
        payload, seconds, server_seconds = engine.synthesize('Hello there, reader.', 'narrator')
        self.assertGreater(seconds, 0)
        self.assertAlmostEqual(server_seconds, 0.125)
        self.assertEqual(self.server.speech.calls[-1], ('Hello there, reader.', 'narrator'))
        with wave.open(io.BytesIO(payload)) as audio:
            self.assertEqual(audio.getframerate(), 24000)

    def test_save_writes_a_wav_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / '00000.wav'
            seconds = backends.ServerEngine(self.server.url, TOKEN, 5).save(str(target), 'Some words.')
            self.assertTrue(target.exists())
            self.assertGreater(seconds, 0)
            self.assertFalse(Path(str(target) + '.part').exists())

    def test_wrong_token_is_refused_without_echoing_it(self):
        with self.assertRaises(backends.ServerError) as caught:
            backends.ServerEngine(self.server.url, 'wrong-secret', 5).health()
        self.assertIn('refused the token', str(caught.exception))
        self.assertNotIn('wrong-secret', str(caught.exception))

    def test_openai_compatible_endpoint(self):
        import urllib.request
        body = json.dumps({'model': 'phonon', 'input': 'Compatible speech.', 'voice': 'alloy', 'response_format': 'wav'}).encode()
        request = urllib.request.Request(self.server.url + '/v1/audio/speech', data=body, method='POST',
                                         headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.headers['Content-Type'], 'audio/wav')
            self.assertEqual(response.read()[:4], b'RIFF')
        self.assertEqual(self.server.speech.calls[-1], ('Compatible speech.', None))

    def test_server_rejects_bad_requests(self):
        engine = backends.ServerEngine(self.server.url, TOKEN, 5)
        for path, body in (('/v1/tts', {'text': ''}), ('/v1/tts', {'text': 'x' * 6000}),
                           ('/v1/tts', {'text': 'Hi.', 'voice': 'nobody'}),
                           ('/v1/audio/speech', {'input': 'Hi.', 'response_format': 'mp3'})):
            with self.assertRaises(backends.ServerError, msg=body):
                engine._request(path, body)
        with self.assertRaises(backends.ServerError) as caught:
            engine._request('/v1/unknown', {'text': 'Hi.'})
        self.assertIn('not a Runespeak speech server', str(caught.exception))

    def test_connection_check_reports_speed(self):
        message = backends.test_connection(self.server.url, TOKEN, 5)
        self.assertIn('Connected', message)
        self.assertIn('24000 Hz', message)

    def test_unreachable_server_raises_a_short_error(self):
        with self.assertRaises(backends.ServerError) as caught:
            backends.ServerEngine(closed_port_url(), TOKEN, 2).health()
        self.assertIn('unreachable', str(caught.exception))

    def test_server_requires_token_on_network_address(self):
        module = self.server.module
        with patch.dict(os.environ, {'RUNESPEAK_SERVER_TOKEN': '', 'RUNESPEAK_SERVER_TOKEN_FILE': ''}), \
                patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            module.main(['--host', '0.0.0.0'])


class FakeTTS:
    loads = 0

    def __init__(self, *args, **kwargs):
        FakeTTS.loads += 1

    def save(self, path, text, voice=None):
        Path(path).write_bytes(tone_wav(0.2))
        return 0.2


class WorkerEngineTests(unittest.TestCase):
    def run_worker(self, engine, environment=None):
        FakeTTS.loads = 0
        fake = types.SimpleNamespace(TTS=FakeTTS, set_num_threads=lambda n: None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        job = Path(folder.name) / 'job.json'
        job.write_text(json.dumps({'chunks': ['One.', 'Two.'], 'directory': folder.name, 'engine': engine}))
        out = io.StringIO()
        with patch.dict(sys.modules, {'ptts': fake}), patch.object(sys, 'argv', ['worker', str(job)]), \
                patch.object(worker, 'lower_priority'), patch('sys.stdout', out), \
                patch.dict(os.environ, environment or {}):
            worker.main()
        return out.getvalue().splitlines(), Path(folder.name)

    def test_server_engine_used_without_loading_the_local_model(self):
        server = MockServer()
        self.addCleanup(server.close)
        lines, folder = self.run_worker({'kind': 'server', 'url': server.url, 'timeout': 5},
                                        {backends.TOKEN_ENV: TOKEN})
        self.assertEqual([line for line in lines if line.isdigit()], ['1', '2'])
        self.assertFalse(any(line.startswith('notice') for line in lines))
        self.assertEqual(FakeTTS.loads, 0)
        self.assertEqual(len(server.speech.calls), 2)
        self.assertTrue((folder / '00001.wav').exists())
        self.assertNotIn(backends.TOKEN_ENV, os.environ, 'token is not left in the environment')

    def test_falls_back_to_this_device_with_a_notice(self):
        lines, folder = self.run_worker({'kind': 'server', 'url': closed_port_url(), 'timeout': 2})
        notices = [line for line in lines if line.startswith('notice ')]
        self.assertEqual(len(notices), 1, 'one notice, then the rest is local')
        self.assertIn('AI server unreachable, narrating on', notices[0])
        self.assertEqual([line for line in lines if line.isdigit()], ['1', '2'])
        self.assertEqual(FakeTTS.loads, 1)

    def test_local_engine_by_default(self):
        lines, _ = self.run_worker({'kind': 'local'})
        self.assertEqual(FakeTTS.loads, 1)
        self.assertTrue(any(line.startswith('timing 1 ') for line in lines))


class FlakyServer:
    """A mock speech server whose behavior can be switched while a narration runs."""

    def __init__(self, port=0):
        from http.server import BaseHTTPRequestHandler
        owner = self
        self.mode = 'ok'
        self.requests = []
        self.on_request = None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self):
                owner.requests.append(self.path)
                if owner.on_request:
                    owner.on_request(self.path)
                mode = owner.mode
                if mode == 'drop':                 # Server dies mid-request.
                    self.connection.shutdown(2)
                    self.close_connection = True
                    return
                if mode == 'hang':
                    time.sleep(2.5)
                code, body, kind = {'500': (500, b'{}', 'application/json'),
                                    '401': (401, b'{}', 'application/json')}.get(mode, (200, None, None))
                if body is None:
                    if self.path == '/v1/health':
                        body, kind = json.dumps({'model': 'mock', 'sample_rate': 24000, 'voices': []}).encode(), 'application/json'
                    else:
                        length = int(self.headers.get('Content-Length', 0))
                        self.rfile.read(length)
                        body, kind = tone_wav(0.2), 'audio/wav'
                self.send_response(code)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = respond

        self.httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.httpd.handle_error = lambda *args: None       # Dropped connections are the point.
        self.port = self.httpd.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FailureModeTests(unittest.TestCase):
    """Every way the server can be unreachable ends in a quick, clear ServerError."""

    def setUp(self):
        self.server = FlakyServer()

    def tearDown(self):
        self.server.stop()

    def engine(self, timeout=5):
        return backends.ServerEngine(self.server.url, TOKEN, timeout)

    def assert_fails(self, needle, call):
        started = time.monotonic()
        with self.assertRaises(backends.ServerError) as caught:
            call()
        self.assertIn(needle, str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))
        return time.monotonic() - started

    def test_short_connect_timeout_by_default(self):
        self.assertEqual(self.engine(timeout=20).connect_timeout, 2.0)

    def test_name_not_found(self):
        import socket
        engine = backends.ServerEngine('http://runespeak-test.invalid:8790', TOKEN, 5)
        with patch('http.client.socket.create_connection', side_effect=socket.gaierror(8, 'nodename nor servname provided')):
            self.assert_fails('name not found', lambda: engine.health())

    def test_connection_refused(self):
        self.assert_fails('connection refused', lambda: backends.ServerEngine(closed_port_url(), TOKEN, 5).health())

    def test_connect_timeout(self):
        def silent(*args, **kwargs):
            raise TimeoutError('timed out')
        with patch('http.client.socket.create_connection', side_effect=silent):
            self.assert_fails('no answer within 2 s', lambda: self.engine().health())

    def test_read_timeout_when_server_hangs(self):
        self.server.mode = 'hang'
        took = self.assert_fails('stopped responding', lambda: backends.ServerEngine(self.server.url, TOKEN, 0.5).synthesize('Hi.'))
        self.assertLess(took, 1.4)

    def test_server_error_and_refused_token(self):
        self.server.mode = '500'
        self.assert_fails('server error HTTP 500', lambda: self.engine().synthesize('Hi.'))
        self.server.mode = '401'
        self.assert_fails('refused the token', lambda: self.engine().health())

    def test_connection_dropped_mid_request(self):
        self.server.mode = 'drop'
        self.assert_fails('connection dropped', lambda: self.engine().synthesize('Hi.'))


class HealthCacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_STATE_HOME': self.folder.name})
        self.env.start()
        self.server = FlakyServer()

    def tearDown(self):
        self.server.stop()
        self.env.stop()
        self.folder.cleanup()

    def test_result_is_reused_briefly_then_rechecked(self):
        engine = backends.ServerEngine(self.server.url, TOKEN, 5)
        now = [1000.0]
        self.assertTrue(backends.cached_health(engine, clock=lambda: now[0]))
        self.assertTrue(backends.cached_health(engine, clock=lambda: now[0] + 5))
        self.assertEqual(self.server.requests.count('/v1/health'), 1, 'cached, not probed again')
        self.server.mode = '500'
        now[0] += 60
        with self.assertRaises(backends.ServerError):
            backends.cached_health(engine, clock=lambda: now[0])
        with self.assertRaises(backends.ServerError):     # Failure is cached too.
            backends.cached_health(engine, clock=lambda: now[0] + 1)
        self.assertEqual(self.server.requests.count('/v1/health'), 2)


class SlowFakeTTS(FakeTTS):
    def save(self, path, text, voice=None):
        time.sleep(0.12)
        return super().save(path, text, voice)


class FallbackAndReturnTests(unittest.TestCase):
    """Whole narrations through the worker: every passage made exactly once, in order."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_STATE_HOME': self.folder.name, backends.TOKEN_ENV: TOKEN})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def narrate(self, url, count, on_request=None, reprobe=0.2):
        SlowFakeTTS.loads = 0
        fake = types.SimpleNamespace(TTS=SlowFakeTTS, set_num_threads=lambda n: self.threads.append(n))
        self.threads = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        chunks = [f'Passage {index}.' for index in range(count)]
        job = Path(directory.name) / 'job.json'
        job.write_text(json.dumps({'chunks': chunks, 'directory': directory.name,
                                   'engine': {'kind': 'server', 'url': url, 'timeout': 2}}))
        out = io.StringIO()
        with patch.dict(sys.modules, {'ptts': fake}), patch.object(sys, 'argv', ['worker', str(job)]), \
                patch.object(worker, 'lower_priority') as nice, patch('sys.stdout', out):
            worker.main(reprobe_seconds=reprobe)
        lines = out.getvalue().splitlines()
        engines = [line.split()[4] for line in lines if line.startswith('timing ')]
        sections = [int(line) for line in lines if line.isdigit()]
        self.assertEqual(sections, list(range(1, count + 1)), 'every passage once, in order')
        self.assertEqual(sorted(p.name for p in Path(directory.name).glob('*.wav')),
                         [f'{index:05d}.wav' for index in range(count)])
        nice.assert_called_once()
        return lines, engines

    def notices(self, lines):
        return [line[7:] for line in lines if line.startswith('notice ')]

    def test_unreachable_at_start_switches_before_the_first_passage(self):
        lines, engines = self.narrate(closed_port_url(), 3)
        self.assertEqual(engines, ['local'] * 3)
        self.assertTrue(self.notices(lines)[0].startswith('AI server unreachable, narrating on'))
        self.assertLess(lines.index(next(l for l in lines if l.startswith('notice'))),
                        lines.index(next(l for l in lines if l.startswith('timing'))))
        self.assertEqual(self.threads, [worker.synthesis_threads()], 'local fallback stays throttled')

    def test_server_drop_mid_document_continues_locally_from_that_passage(self):
        server = FlakyServer()
        self.addCleanup(server.stop)

        def count(path):                   # The third passage request finds the server gone.
            if server.requests.count('/v1/tts') == 3:
                server.mode = 'drop'
        server.on_request = count
        lines, engines = self.narrate(server.url, 5, reprobe=60)
        self.assertEqual(engines, ['server', 'server', 'local', 'local', 'local'])
        self.assertEqual(len(self.notices(lines)), 1)

    def test_errors_and_timeouts_mid_document_fall_back(self):
        for mode in ('500', '401', 'hang'):
            with self.subTest(mode=mode):
                server = FlakyServer()
                self.addCleanup(server.stop)

                def count(path, server=server, mode=mode):
                    if server.requests.count('/v1/tts') == 2:
                        server.mode = mode
                server.on_request = count
                lines, engines = self.narrate(server.url, 3, reprobe=60)
                self.assertEqual(engines, ['server', 'local', 'local'])

    def test_returns_to_the_server_at_a_passage_boundary(self):
        server = FlakyServer()
        port = server.port
        server.stop()                                   # Service stopped at the start.
        restarted = {}

        def restart():
            time.sleep(0.3)
            restarted['server'] = FlakyServer(port)     # Service started again.
        threading.Thread(target=restart, daemon=True).start()
        lines, engines = self.narrate(f'http://127.0.0.1:{port}', 12, reprobe=0.2)
        self.addCleanup(lambda: restarted['server'].stop())
        self.assertEqual(engines[0], 'local')
        self.assertEqual(engines[-1], 'server')
        switch = engines.index('server')
        self.assertEqual(engines, ['local'] * switch + ['server'] * (12 - switch), 'one clean switch back')
        notices = self.notices(lines)
        self.assertTrue(notices[0].startswith('AI server unreachable'))
        self.assertEqual(notices[-1], backends.BACK_NOTICE)


class CompareTests(unittest.TestCase):
    def test_local_timing_skips_warm_up_and_reads_worker_timing(self):
        from runespeak import engine_dialog
        with tempfile.TemporaryDirectory() as folder:
            def runner(command, **kwargs):
                job = json.loads(Path(command[-1]).read_text())
                self.assertEqual(job['chunks'][0], 'Ready.')
                self.assertEqual(job['engine'], {'kind': 'local'})
                (Path(folder) / '00001.wav').write_bytes(tone_wav())
                return subprocess.CompletedProcess(command, 0, 'timing 0 5.0 0.4\n1\ntiming 1 1.500 3.000\n2\n', '')
            path, took, seconds = engine_dialog.local_timing('A passage.', 'default', folder, runner)
            self.assertEqual((took, seconds), (1.5, 3.0))
            self.assertTrue(path.exists())
        self.assertIn('real-time factor 0.50', engine_dialog.describe('A', 1.5, 3.0))

    def test_server_timing(self):
        from runespeak import engine_dialog
        server = MockServer()
        self.addCleanup(server.close)
        with tempfile.TemporaryDirectory() as folder:
            path, took, seconds, server_seconds = engine_dialog.server_timing(
                backends.ServerEngine(server.url, TOKEN, 5), 'A passage to compare.', 'default', folder)
            self.assertTrue(path.exists())
            self.assertGreater(seconds, 0)
            self.assertAlmostEqual(server_seconds, 0.125)


if __name__ == '__main__':
    unittest.main()
