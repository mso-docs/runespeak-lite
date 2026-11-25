#!/usr/bin/env python3
"""Reference speech server for Runespeak Lite (standard library plus ``ptts``).

Runs the same Phonon 31M English model the app uses on its own, so a more powerful
computer on your network can do the synthesis. See docs/SPEECH-SERVER.md.

    RUNESPEAK_SERVER_TOKEN_FILE=~/.config/runespeak-server/token \
        python3 runespeak-server.py --host 192.0.2.10 --port 8790

Endpoints: GET /v1/health, POST /v1/tts, POST /v1/audio/speech (OpenAI-compatible).
"""
import argparse
import hmac
import io
import json
import os
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = 'hf://gradium/phonon-31m-en'
MODEL_NAME = 'phonon-31m-en'
MAX_TEXT = 5000
MAX_BODY = 64 * 1024


class Speech:
    """One loaded model; requests take turns so memory and threads stay bounded."""

    def __init__(self, quant, threads):
        import ptts
        if threads:
            ptts.set_num_threads(threads)
        self.tts = ptts.TTS(MODEL, lang='en', quant=quant)
        self.lock = threading.Lock()
        self.rate = int(self.tts.sample_rate)
        try:
            self.voices = list(self.tts.voices)
        except Exception:
            self.voices = []

    def wav(self, text, voice=None):
        import numpy
        with self.lock:
            started = time.monotonic()
            samples = self.tts.synth(text, voice=voice) if voice else self.tts.synth(text)
            took = time.monotonic() - started
        pcm = numpy.clip(numpy.asarray(samples, dtype=numpy.float32), -1.0, 1.0)
        data = (pcm * 32767.0).round().astype('<i2').tobytes()
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(self.rate)
            audio.writeframes(data)
        return buffer.getvalue(), took


def make_handler(speech, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'runespeak-server/1'

        def log_message(self, format, *args):   # One line per request, never headers.
            sys.stderr.write('%s %s\n' % (self.address_string(), format % args))

        def authorized(self):
            if not token:
                return True
            supplied = self.headers.get('Authorization', '')
            return hmac.compare_digest(supplied.encode(), ('Bearer ' + token).encode())

        def reply(self, code, body, kind='application/json', headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.authorized():
                return self.reply(401, {'error': 'unauthorized'})
            if self.path.rstrip('/') == '/v1/health':
                return self.reply(200, {'model': MODEL_NAME, 'sample_rate': speech.rate, 'voices': speech.voices})
            self.reply(404, {'error': 'not found'})

        def do_POST(self):
            if not self.authorized():
                return self.reply(401, {'error': 'unauthorized'})
            path = self.path.rstrip('/')
            if path not in ('/v1/tts', '/v1/audio/speech'):
                return self.reply(404, {'error': 'not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if not 0 < length <= MAX_BODY:
                return self.reply(413 if length else 400, {'error': 'body missing or too large'})
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                return self.reply(400, {'error': 'invalid JSON'})
            text = body.get('text') if path == '/v1/tts' else body.get('input')
            voice = body.get('voice') or None
            if voice in ('default', 'alloy'):     # Accept OpenAI's default voice name.
                voice = None
            if path == '/v1/audio/speech' and body.get('response_format', 'wav') not in ('wav', 'pcm'):
                return self.reply(400, {'error': 'only wav or pcm output is supported'})
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
                return self.reply(400, {'error': f'text must be 1-{MAX_TEXT} characters'})
            if voice is not None and speech.voices and voice not in speech.voices:
                return self.reply(400, {'error': 'unknown voice'})
            try:
                audio, took = speech.wav(text.strip(), voice)
            except Exception as exc:
                return self.reply(500, {'error': f'synthesis failed: {type(exc).__name__}'})
            if path == '/v1/audio/speech' and body.get('response_format') == 'pcm':
                return self.reply(200, audio[44:], 'audio/pcm', {'X-Sample-Rate': str(speech.rate)})
            self.reply(200, audio, 'audio/wav', {'X-Sample-Rate': str(speech.rate),
                                                 'X-Synthesis-Seconds': f'{took:.3f}'})
    return Handler


def read_token(args):
    path = args.token_file or os.environ.get('RUNESPEAK_SERVER_TOKEN_FILE')
    if path:
        return Path(path).expanduser().read_text().strip()
    return os.environ.get('RUNESPEAK_SERVER_TOKEN', '').strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', default='127.0.0.1', help='address to listen on (default: this computer only)')
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--token-file', help='file holding the bearer token (or set RUNESPEAK_SERVER_TOKEN_FILE)')
    parser.add_argument('--quant', default='f32', help='model weight format (f32 is fastest on x86 CPUs)')
    parser.add_argument('--threads', type=int, default=0, help='CPU threads for the model (default: runtime choice)')
    args = parser.parse_args(argv)
    token = read_token(args)
    if not token and args.host not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('a token is required when listening on a network address')
    speech = Speech(args.quant, args.threads)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(speech, token))
    print(f'Runespeak speech server on http://{args.host}:{args.port} ({MODEL_NAME}, {speech.rate} Hz)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
