"""Speech engines: this device (local Phonon) or a local AI server on your network.

Server API (version 1), documented in docs/SPEECH-SERVER.md:

* ``GET  /v1/health`` returns JSON ``{"model", "sample_rate", "voices"}``.
* ``POST /v1/tts`` with JSON ``{"text", "voice"?, "rate"?}`` returns ``audio/wav``
  (16-bit PCM) with ``X-Sample-Rate`` and ``X-Synthesis-Seconds`` headers.
* ``POST /v1/audio/speech`` accepts the OpenAI-compatible body
  ``{"model", "input", "voice", "response_format": "wav"}``.

Requests carry ``Authorization: Bearer <token>`` when a token is set. The token is
never written to logs, job files or error messages.
"""
import http.client
import io
import json
import os
import socket
import stat
import threading
import sys
import time
import urllib.parse
import wave

from .platforms import config_dir, is_macos, state_dir

LOCAL = 'local'
SERVER = 'server'
DEFAULT_TIMEOUT = 20.0      # Longest wait for one passage once connected.
CONNECT_TIMEOUT = 2.0       # An unreachable server is detected this quickly.
HEALTH_TIMEOUT = 3.0
HEALTH_CACHE_SECONDS = 20.0
REPROBE_SECONDS = 30.0
TOKEN_ENV = 'RUNESPEAK_SERVER_TOKEN'
KEYRING_SERVICE = 'Runespeak Lite AI server'


class ServerError(RuntimeError):
    pass


def engine_settings(preferences):
    """The chosen engine from preferences, with safe defaults."""
    value = preferences.get('engine') if isinstance(preferences.get('engine'), dict) else {}
    kind = value.get('kind') if value.get('kind') in (LOCAL, SERVER) else LOCAL
    url = value.get('url') if isinstance(value.get('url'), str) else ''
    try:
        timeout = min(300.0, max(2.0, float(value.get('timeout', DEFAULT_TIMEOUT))))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return {'kind': kind if (kind == LOCAL or url) else LOCAL, 'url': url.strip(), 'timeout': timeout}


def normalize_url(url):
    url = (url or '').strip()
    if url and '://' not in url:
        url = 'http://' + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('Enter the server address, for example http://192.0.2.10:8790')
    return url.rstrip('/')


# Token storage: the system keychain when the keyring package is present, otherwise a
# file only you can read (0600) in the settings folder.

def token_path():
    return config_dir() / 'server-token'


def _keyring():
    try:
        import keyring
        keyring.get_keyring()
        return keyring
    except Exception:
        return None


def load_token():
    ring = _keyring()
    if ring:
        try:
            value = ring.get_password(KEYRING_SERVICE, 'token')
            if value:
                return value
        except Exception:
            pass
    try:
        return token_path().read_text().strip()
    except OSError:
        return ''


def save_token(token):
    """Store (or with an empty token, forget) the bearer token. Returns where it went."""
    token = (token or '').strip()
    ring = _keyring()
    if ring:
        try:
            if token:
                ring.set_password(KEYRING_SERVICE, 'token', token)
            else:
                try:
                    ring.delete_password(KEYRING_SERVICE, 'token')
                except Exception:
                    pass
            try:
                token_path().unlink()
            except OSError:
                pass
            return 'keychain'
        except Exception:
            pass
    path = token_path()
    if not token:
        try:
            path.unlink()
        except OSError:
            pass
        return 'none'
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(token + '\n')
    if sys.platform != 'win32':
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return 'file'


class ServerEngine:
    """Client for the documented server API."""

    def __init__(self, url, token='', timeout=DEFAULT_TIMEOUT, connect_timeout=CONNECT_TIMEOUT):
        self.url = normalize_url(url)
        self.token = token or ''
        self.timeout = timeout
        self.connect_timeout = min(connect_timeout, timeout)
        parts = urllib.parse.urlsplit(self.url)
        self.secure = parts.scheme == 'https'
        self.host = parts.hostname
        self.port = parts.port or (443 if self.secure else 80)
        self.prefix = parts.path.rstrip('/')

    def _request(self, path, body=None, timeout=None):
        """One request with a short connect timeout (an unreachable server is noticed in
        about two seconds) and a longer read timeout for synthesis."""
        headers = {'Accept': 'application/json, audio/wav', 'User-Agent': 'runespeak-lite', 'Connection': 'close'}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        kind = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
        connection = kind(self.host, self.port, timeout=self.connect_timeout)
        try:
            try:
                connection.connect()
            except socket.gaierror:
                raise ServerError('AI server unreachable (name not found; is the network or VPN connected?).') from None
            except (TimeoutError, socket.timeout):
                raise ServerError('AI server unreachable (no answer within '
                                  f'{self.connect_timeout:g} s).') from None
            except ConnectionRefusedError:
                raise ServerError('AI server unreachable (connection refused; is the service running?).') from None
            except OSError as exc:
                raise ServerError(f'AI server unreachable ({type(exc).__name__}).') from None
            connection.sock.settimeout(timeout or self.timeout)
            try:
                connection.request('POST' if data is not None else 'GET', self.prefix + path, body=data, headers=headers)
                response = connection.getresponse()
                payload = response.read()
            except (TimeoutError, socket.timeout):
                raise ServerError('AI server stopped responding (timed out).') from None
            except (http.client.HTTPException, OSError) as exc:
                raise ServerError(f'AI server connection dropped ({type(exc).__name__}).') from None
            if response.status >= 400:
                reason = {401: 'the server refused the token', 403: 'the server refused the token',
                          404: 'this address is not a Runespeak speech server'}.get(
                    response.status, f'server error HTTP {response.status}' if response.status >= 500 else f'HTTP {response.status}')
                raise ServerError(f'AI server: {reason}.')
            return payload, dict(response.getheaders())
        finally:
            connection.close()

    def health(self, timeout=HEALTH_TIMEOUT):
        payload, _ = self._request('/v1/health', timeout=timeout)
        try:
            info = json.loads(payload)
        except ValueError:
            raise ServerError('AI server: unexpected health response.') from None
        if not isinstance(info, dict) or 'sample_rate' not in info:
            raise ServerError('AI server: unexpected health response.')
        return info

    def synthesize(self, text, voice=None):
        """Return (wav_bytes, seconds_of_audio, server_seconds)."""
        body = {'text': text}
        if voice and voice != 'default':
            body['voice'] = voice
        payload, headers = self._request('/v1/tts', body)
        try:
            with wave.open(io.BytesIO(payload)) as audio:
                seconds = audio.getnframes() / audio.getframerate()
                if audio.getsampwidth() != 2:
                    raise ServerError('AI server: audio must be 16-bit PCM WAV.')
        except (wave.Error, EOFError):
            raise ServerError('AI server: the reply was not WAV audio.') from None
        lowered = {key.lower(): value for key, value in headers.items()}
        try:
            server_seconds = float(lowered.get('x-synthesis-seconds', 'nan'))
        except ValueError:
            server_seconds = float('nan')
        return payload, seconds, server_seconds

    def save(self, path, text, voice=None):
        payload, seconds, _ = self.synthesize(text, voice)
        temporary = f'{path}.part'
        with open(temporary, 'wb') as handle:
            handle.write(payload)
        os.replace(temporary, path)
        return seconds


def test_connection(url, token='', timeout=10.0):
    """A short human-readable verdict for the Test connection button."""
    engine = ServerEngine(url, token, timeout)
    info = engine.health()
    started = time.monotonic()
    _, seconds, _ = engine.synthesize('Testing the connection.')
    took = time.monotonic() - started
    return (f"Connected: {info.get('model', 'speech model')} at {info.get('sample_rate')} Hz. "
            f"A test phrase took {took:.1f} s for {seconds:.1f} s of speech.")


def this_computer():
    return 'this Mac' if is_macos() else 'this computer'


def fallback_notice():
    return f'AI server unreachable, narrating on {this_computer()}.'


BACK_NOTICE = 'Back on AI server.'


def health_cache_path():
    return state_dir() / 'server-health.json'


def cached_health(engine, ttl=HEALTH_CACHE_SECONDS, clock=time.time):
    """True when the server answered its health check recently (or answers now).
    The result is shared between narrations for a few seconds, so starting, stopping
    and starting again does not probe every time. Raises ServerError when unhealthy."""
    path = health_cache_path()
    try:
        record = json.loads(path.read_text())
        if record.get('url') == engine.url and 0 <= clock() - float(record.get('at', 0)) < ttl:
            if record.get('ok'):
                return True
            raise ServerError(record.get('error') or 'AI server unreachable.')
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        engine.health()
        record = {'url': engine.url, 'at': clock(), 'ok': True}
    except ServerError as exc:
        record = {'url': engine.url, 'at': clock(), 'ok': False, 'error': str(exc)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(record))
        temporary.replace(path)
    except OSError:
        pass
    if not record['ok']:
        raise ServerError(record['error'])
    return True


class Reprober:
    """Checks a down server in the background every ``interval`` seconds. ``ready`` is
    set once it answers, so the narration can return to it at the next passage."""

    def __init__(self, engine, interval=REPROBE_SECONDS):
        self.engine = engine
        self.interval = interval
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped.wait(self.interval):
            try:
                self.engine.health()
            except ServerError:
                continue
            self.ready.set()
            return

    def stop(self):
        self.stopped.set()
