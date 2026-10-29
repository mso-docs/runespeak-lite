import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from runespeak.bridge import CommandServer, native_message, request, validate


class BridgeTests(unittest.TestCase):
    def test_roundtrip_and_closed_app(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'app.sock'
            commands = []
            def receive(message):
                commands.append(message)
                return {'ok': True}
            server = CommandServer(receive, path)
            try:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(request({'action': 'read', 'text': 'Selected words'}, path), {'ok': True})
                self.assertEqual(commands[0]['text'], 'Selected words')
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    CommandServer(receive, path)
            finally:
                server.close()
            self.assertFalse(path.exists())
            with self.assertRaisesRegex(RuntimeError, 'Open Runespeak'):
                request({'action': 'clipboard'}, path)

    def test_rejects_invalid_messages(self):
        for message in ([], {}, {'action': 'launch'}, {'action': 'read', 'text': ''}, {'action': 'read', 'text': 42}):
            with self.assertRaises(ValueError):
                validate(message)

    def test_native_message_framing(self):
        message = {'action': 'read', 'text': 'Unicode selection: café'}
        payload = json.dumps(message).encode()
        output = io.BytesIO()
        with patch('runespeak.bridge.request', return_value={'ok': True}) as send:
            native_message(io.BytesIO(struct.pack('=I', len(payload)) + payload), output)
        send.assert_called_once_with(message)
        data = output.getvalue()
        self.assertEqual(struct.unpack('=I', data[:4])[0], len(data) - 4)
        self.assertEqual(json.loads(data[4:]), {'ok': True})

    def test_native_message_rejects_oversized_payload(self):
        output = io.BytesIO()
        with patch('runespeak.bridge.request') as send:
            native_message(io.BytesIO(struct.pack('=I', 1_000_001)), output)
        send.assert_not_called()
        self.assertFalse(json.loads(output.getvalue()[4:])['ok'])
