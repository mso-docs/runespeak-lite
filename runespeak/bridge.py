"""Per-user command channel, CLI shortcut client, and Firefox native message host.

Linux and macOS use a private Unix socket. Windows Python has no Unix sockets, so the
app listens on 127.0.0.1 only, on a random port, and every request must carry a random
per-session token stored in the user's private app-data folder. There is no browser
page listener, automatic app launch, or clipboard watcher.
"""
import argparse
import hmac
import json
import os
import secrets
from pathlib import Path
import socket
import socketserver
import stat
import struct
import sys
import threading

from .platforms import is_windows

MAX_MESSAGE = 1_000_000
HOST_NAME = 'org.runespeak.lite'
EXTENSION_ID = 'runespeak-lite@local'


def endpoint_path() -> Path:
    """Windows: where the running app records its loopback port and session token."""
    base = os.environ.get('RUNESPEAK_RUNTIME_DIR') or os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    directory = Path(base) / 'runespeak-lite'
    directory.mkdir(parents=True, exist_ok=True)
    return directory / 'bridge.json'


def socket_path() -> Path:
    runtime = os.environ.get('RUNESPEAK_RUNTIME_DIR') or os.environ.get('XDG_RUNTIME_DIR')
    base = Path(runtime) if runtime else Path('/tmp')
    directory = base / f'runespeak-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('Runespeak runtime directory must be private and owned by your user.')
    return directory / 'app.sock'


def validate(message):
    if not isinstance(message, dict):
        raise ValueError('Expected a command object')
    if message.get('action') not in ('read', 'clipboard', 'pause', 'resume', 'stop', 'restart', 'open'):
        raise ValueError('Unknown command')
    if message['action'] == 'open' and not isinstance(message.get('path'), str):
        raise ValueError('Invalid audio path')
    if message['action'] == 'read':
        text = message.get('text')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Select some text first.')
        if not isinstance(message.get('source', ''), str):
            raise ValueError('Invalid source URL')
    return message


def request(message, path=None):
    validate(message)
    data = json.dumps(message).encode() + b'\n'
    if len(data) > MAX_MESSAGE:
        raise ValueError('Selected text exceeds the 1 MB message limit.')
    try:
        if is_windows() and path is None:
            endpoint = json.loads(endpoint_path().read_text())
            data = json.dumps(dict(message, token=endpoint['token'])).encode() + b'\n'
            client = socket.create_connection(('127.0.0.1', int(endpoint['port'])), timeout=5)
        else:
            client = socket.socket(socket.AF_UNIX)
            client.settimeout(5)
            client.connect(str(path or socket_path()))
        with client:
            client.sendall(data)
            with client.makefile('rb') as stream:
                response = stream.readline(MAX_MESSAGE + 1)
            return json.loads(response)
    except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise RuntimeError('Open Runespeak Lite first. The app must be running to read selections.') from exc


class CommandServer:
    def __init__(self, callback, path=None):
        self.token = None
        if is_windows() and path is None:
            self.path = endpoint_path()
            self._check_loopback_instance()
            self.token = secrets.token_urlsafe(32)
        else:
            self.path = Path(path) if path else socket_path()
            self._check_unix_instance()
        token = self.token

        class Handler(socketserver.StreamRequestHandler):
            def handle(inner):
                inner.connection.settimeout(5)
                try:
                    data = inner.rfile.readline(MAX_MESSAGE + 1)
                    if len(data) > MAX_MESSAGE or not data.endswith(b'\n'):
                        raise ValueError('Invalid or oversized command')
                    message = json.loads(data)
                    if token is not None:
                        supplied = message.pop('token', None) if isinstance(message, dict) else None
                        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, token):
                            raise ValueError('Unauthorized command')
                    result = callback(validate(message))
                except Exception as exc:
                    result = {'ok': False, 'error': str(exc)}
                try:
                    inner.wfile.write(json.dumps(result).encode() + b'\n')
                except OSError:
                    pass
        if token is not None:
            class Server(socketserver.ThreadingTCPServer):
                daemon_threads = True
                allow_reuse_address = False
            self.server = Server(('127.0.0.1', 0), Handler)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'port': self.server.server_address[1], 'token': token, 'pid': os.getpid()}))
            temporary.replace(self.path)
        else:
            class Server(socketserver.ThreadingUnixStreamServer):
                daemon_threads = True
            self.server = Server(str(self.path), Handler)
            self.path.chmod(0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _check_loopback_instance(self):
        try:
            endpoint = json.loads(self.path.read_text())
            with socket.create_connection(('127.0.0.1', int(endpoint['port'])), timeout=1):
                pass
        except (OSError, ValueError, KeyError, TypeError):
            return  # No live instance; the stale file is overwritten.
        raise RuntimeError('Runespeak Lite is already running.')

    def _check_unix_instance(self):
        # Never remove another live instance's socket.
        if self.path.exists():
            with socket.socket(socket.AF_UNIX) as probe:
                try:
                    probe.connect(str(self.path))
                except ConnectionRefusedError:
                    self.path.unlink()
                else:
                    raise RuntimeError('Runespeak Lite is already running.')

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.path.unlink(missing_ok=True)


def read_exact(stream, length):
    data = bytearray()
    while len(data) < length:
        block = stream.read(length - len(data))
        if not block:
            raise ValueError('Incomplete native message')
        data.extend(block)
    return bytes(data)


def native_message(input_stream, output_stream):
    try:
        length = struct.unpack('=I', read_exact(input_stream, 4))[0]
        if length > MAX_MESSAGE:
            raise ValueError('Selected text exceeds the 1 MB message limit.')
        message = json.loads(read_exact(input_stream, length))
        result = request(message)
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    payload = json.dumps(result).encode()
    output_stream.write(struct.pack('=I', len(payload)) + payload)
    output_stream.flush()


def main():
    parser = argparse.ArgumentParser(description='Read copied text in the already-open Runespeak app')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--clipboard', action='store_true')
    modes.add_argument('--text')
    for action in ('pause', 'resume', 'stop', 'restart', 'native-host'):
        modes.add_argument('--' + action, action='store_true')
    options, _ = parser.parse_known_args()  # Firefox supplies native-host metadata arguments.
    if options.native_host:
        native_message(sys.stdin.buffer, sys.stdout.buffer)
        return
    message = {'action': 'clipboard'}
    if options.text is not None:
        message = {'action': 'read', 'text': options.text}
    for action in ('pause', 'resume', 'stop', 'restart'):
        if getattr(options, action):
            message = {'action': action}
    try:
        result = request(message)
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'Command failed'))
        print('Sent to Runespeak Lite')
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        # Shortcut invocations have no terminal; show failure using desktop notifications.
        import shutil
        import subprocess
        if sys.platform == 'darwin':
            script = 'display notification ' + json.dumps(str(exc)) + ' with title "Runespeak Lite"'
            subprocess.run(['osascript', '-e', script], check=False)
        elif shutil.which('notify-send'):
            subprocess.run(['notify-send', 'Runespeak Lite', str(exc)], check=False)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
