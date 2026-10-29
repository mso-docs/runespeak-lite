"""Text preparation and lossless narration assembly."""
import re
import wave
from pathlib import Path


def chunks(text: str, limit: int = 180) -> list[str]:
    """Short speech passages: prefer one sentence, cap long ones at a clause/word.

    The same passages drive synthesis and highlighting, so follow-along never
    depends on guessing word timestamps inside a large audio file.
    """
    if limit < 1:
        raise ValueError('Chunk limit must be positive')
    text = re.sub(r"\s+", " ", text).strip()
    result = []
    abbreviations = {'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'fig', 'eq', 'e.g', 'i.e', 'et al'}
    offset = 0
    while offset < len(text):
        window = text[offset:offset+limit+1]
        remaining = len(text)-offset
        end = None
        for match in re.finditer(r'[.!?][\"”’\']*(?=\s|$)', window):
            candidate = match.end()
            if candidate > limit:
                break
            prefix = window[:match.start()]
            word = prefix.split()[-1].lower().strip('(') if prefix.split() else ''
            if window[match.start()] == '.' and (word in abbreviations or (len(word) == 1 and word.isalpha()) or re.fullmatch(r'(?:[a-z]\.)+[a-z]', word)):
                continue
            if candidate >= 25 or candidate == remaining:
                end = candidate
                break
        if end is None:
            if remaining <= limit:
                end = remaining
            else:
                end = max(window.rfind(mark, 0, limit) + 1 for mark in ',;:')
                if end < limit // 2:
                    end = window.rfind(' ', 0, limit+1)
                if end <= 0:
                    end = limit
        part = window[:end].strip()
        if part:
            result.append(part)
        offset += end
        while offset < len(text) and text[offset] == ' ':
            offset += 1
    return result


def combine(parts: list[Path], destination: Path) -> None:
    if not parts:
        raise ValueError("No generated audio")
    with wave.open(str(parts[0]), 'rb') as first:
        params = first.getparams()
    with wave.open(str(destination), 'wb') as output:
        output.setparams(params)
        for part in parts:
            with wave.open(str(part), 'rb') as source:
                if source.getparams()[:3] != params[:3] or source.getcomptype() != params.comptype:
                    raise ValueError("Incompatible audio chunks")
                output.writeframes(source.readframes(source.getnframes()))
