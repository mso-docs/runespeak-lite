# Using a local AI server for speech

Runespeak Lite speaks with the Phonon 31M English model on your own computer by default.
That needs no setup. If you have a more powerful computer on your network (a desktop,
a home server or a workstation), it can make the speech instead. Your laptop then stays
cool and quiet while a long PDF is prepared, and later passages are ready sooner.

The server runs the same model, so the voice is the same. Text you narrate is sent to
that computer, so use a server you control, on your home or private network.

## Set up the server

On the server computer (Linux, macOS or Windows with Python 3.10 or newer):

```bash
mkdir -p ~/runespeak-server && cd ~/runespeak-server
python3 -m venv .venv
.venv/bin/pip install ptts numpy
curl -LO https://raw.githubusercontent.com/mso-docs/runespeak-lite/main/scripts/runespeak-server.py
# Create a random access token readable only by you.
mkdir -p ~/.config/runespeak-server
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > ~/.config/runespeak-server/token
chmod 600 ~/.config/runespeak-server/token
# Listen on the server's private network address (not 0.0.0.0 on a public network).
.venv/bin/python runespeak-server.py --host 192.0.2.10 --port 8790 \
    --token-file ~/.config/runespeak-server/token
```

The first start downloads the model (about 130 MB). Options:

- `--host` / `--port`: where to listen. The default, `127.0.0.1`, accepts only requests
  from the same computer. A network address requires a token.
- `--token-file` (or `RUNESPEAK_SERVER_TOKEN_FILE`): the bearer token clients must send.
- `--quant`: weight format. `f32` (default) is fastest on Intel/AMD processors; `q8` uses less memory.
- `--threads`: CPU threads for the model.

The Phonon runtime currently runs on the CPU only, so a graphics card is not used.

### Keep it running (Linux, systemd user service)

`~/.config/systemd/user/runespeak-server.service`:

```ini
[Unit]
Description=Runespeak speech server
After=network-online.target

[Service]
WorkingDirectory=%h/runespeak-server
ExecStart=%h/runespeak-server/.venv/bin/python runespeak-server.py --host 192.0.2.10 --port 8790 --token-file %h/.config/runespeak-server/token
Restart=on-failure
Nice=5

[Install]
WantedBy=default.target
```

Then `systemctl --user daemon-reload && systemctl --user enable --now runespeak-server`.
Run `loginctl enable-linger $USER` once if it should keep running while you are logged out.

## Connect the app

Open **Speech → Speech Engine…** (on a Mac also **Runespeak Lite → Settings…**), choose
**Local AI server**, and enter the address (for example `http://192.0.2.10:8790`) and
the token. **Test connection** checks the server and times a short phrase. **Save** keeps
the choice. The token is stored in the system keychain when the `keyring` package is
available, otherwise in `server-token` in the settings folder, readable only by you. It
is never written to the preferences file, job files or logs.

### When the server is unreachable

The setting stays **Local AI server**; switching away from it is only temporary, while
the server is unavailable:

- **At Play** the app checks `GET /v1/health` before the first passage (the answer is
  reused for 20 seconds, so stopping and starting again does not re-check). If the server
  does not answer, the whole narration starts on this computer right away.
- **Unreachable** covers a name that does not resolve (network or VPN down), a refused
  connection (service stopped), no answer within 2 seconds, a server error (HTTP 5xx), a
  refused token (HTTP 401/403), and a server that stops answering for longer than the
  timeout (default 20 s) or drops the connection during a passage.
- **Mid-document** the failed passage is made again on this computer and narration
  continues from it, so nothing is skipped or repeated. Playback is not interrupted;
  later passages are made locally.
- A calm notice under the status line says *AI server unreachable, narrating on this Mac*
  (or *this computer*). It does not interrupt or need a click.
- **Return:** every 30 seconds the app checks the server in the background. When it answers
  again, the next passage is made on the server and the notice briefly says *Back on AI
  server*.
- Local speech during a fallback uses the same gentle settings as always: at most three
  threads, at lower priority, so the computer stays responsive and audio does not crackle.

**Compare A/B** synthesizes the selected text (or the first passage) on this computer (A)
and on the server (B), shows how long each took and the real-time factor (time to make
the speech divided by its length; below 1 is faster than listening), and lets you play
both versions.

## API (version 1)

All requests may carry `Authorization: Bearer <token>`; the reference server requires it
whenever a token is configured.

| Request | Body | Reply |
| --- | --- | --- |
| `GET /v1/health` | none | JSON `{"model": "phonon-31m-en", "sample_rate": 24000, "voices": [...]}` |
| `POST /v1/tts` | JSON `{"text": "...", "voice": "optional voice name"}` | `audio/wav`, mono 16-bit PCM; headers `X-Sample-Rate` and `X-Synthesis-Seconds` |
| `POST /v1/audio/speech` | OpenAI-compatible JSON `{"model": "...", "input": "...", "voice": "...", "response_format": "wav"}` | `audio/wav` (or raw 16-bit PCM for `"pcm"`, with `X-Sample-Rate`) |

Text is limited to 5,000 characters per request. Errors return JSON `{"error": "..."}` with
status 400 (bad request), 401 (missing or wrong token), 404 or 500. Any server that
implements `GET /v1/health` and `POST /v1/tts` this way can be used by the app.
