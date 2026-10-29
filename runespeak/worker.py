"""Isolated synthesis process: terminating it cancels native model work."""
import json
import sys
from pathlib import Path


def main():
    import ptts
    job = json.loads(Path(sys.argv[1]).read_text())
    tts = ptts.TTS('hf://gradium/phonon-31m-en', lang='en', quant='q8')
    for index, text in enumerate(job['chunks']):
        tts.save(str(Path(job['directory']) / f'{index:05d}.wav'), text, voice=None if job.get('voice', 'default') == 'default' else job['voice'])
        print(index + 1, flush=True)


if __name__ == '__main__':
    main()
