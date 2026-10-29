"""Per-user Unix socket, CLI shortcut client, and Firefox native message host.

No TCP port, browser page listener, automatic app launch, or clipboard watcher.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import sys
import threading

MAX_MESSAGE = 1_000_000
HOST_NAME = 'org.runespeak.lite'
EXTENSION_ID = 'runespeak-lite@local'


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
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(str(path or socket_path()))
            client.sendall(data)
            with client.makefile('rb') as stream:
                response = stream.readline(MAX_MESSAGE + 1)
            return json.loads(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError('Open Runespeak Lite first. The app must be running to read selections.') from exc


class CommandServer:
    def __init__(self, callback, path=None):
        self.path = Path(path) if path else socket_path()
        # Never remove another live instance's socket.
        if self.path.exists():
            with socket.socket(socket.AF_UNIX) as probe:
                try:
                    probe.connect(str(self.path))
                except ConnectionRefusedError:
                    self.path.unlink()
                else:
                    raise RuntimeError('Runespeak Lite is already running.')
        class Handler(socketserver.StreamRequestHandler):
            def handle(inner):
                inner.connection.settimeout(5)
                try:
                    data = inner.rfile.readline(MAX_MESSAGE + 1)
                    if len(data) > MAX_MESSAGE or not data.endswith(b'\n'):
                        raise ValueError('Invalid or oversized command')
                    result = callback(validate(json.loads(data)))
                except Exception as exc:
                    result = {'ok': False, 'error': str(exc)}
                try:
                    inner.wfile.write(json.dumps(result).encode() + b'\n')
                except OSError:
                    pass
        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
        self.server = Server(str(self.path), Handler)
        self.path.chmod(0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

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
        if shutil.which('notify-send'):
            subprocess.run(['notify-send', 'Runespeak Lite', str(exc)], check=False)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
