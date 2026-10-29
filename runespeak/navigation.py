"""WAV seeking and explicit heading discovery without guessed word timings."""
from pathlib import Path
import re
import wave


def locate_time(seconds, durations):
    elapsed = 0.0
    for index, length in enumerate(durations):
        if seconds < elapsed + length:
            return index, max(0, seconds-elapsed)
        elapsed += length
    return (len(durations)-1, durations[-1]) if durations else (None, 0)


def audio_slice(source, destination, start, maximum=30):
    with wave.open(str(source), 'rb') as original:
        rate = original.getframerate()
        frame = min(original.getnframes(), max(0, int(start*rate)))
        original.setpos(frame)
        count = min(original.getnframes()-frame, int(maximum*rate))
        with wave.open(str(destination), 'wb') as output:
            output.setparams(original.getparams())
            remaining = count
            while remaining:
                size = min(remaining, 65536)
                output.writeframes(original.readframes(size))
                remaining -= size
        return count / rate


def headings(text):
    return [(match.group(1).strip().rstrip('#').strip(), match.start())
            for match in re.finditer(r'^[ \t]{0,3}#{1,6}[ \t]+(.+)$', text, re.M)]


def heading_targets(source, pieces):
    # The spoken copy omits heading marks. Locate explicit headings in passage
    # order; keep source offsets for jumping ahead of generated audio.
    normalized = ' '.join(pieces)
    offsets = []
    cursor = 0
    for piece in pieces:
        offsets.append(cursor)
        cursor += len(piece)+1
    targets = []
    search_from = 0
    for title, source_offset in headings(source):
        line_end = source.find('\n', source_offset)
        following = ' '.join(source[line_end+1:].split())[:60] if line_end >= 0 else ''
        anchor = ' '.join(title.split()) + (' '+following if following else '')
        position = normalized.find(anchor, search_from)
        if position < 0:
            position = normalized.find(' '.join(title.split()), search_from)
        if position < 0:
            continue
        index = max((i for i, offset in enumerate(offsets) if offset <= position), default=0)
        targets.append((title, source_offset, index))
        search_from = position+len(title)
    return targets


def reading_context(full_spoken, selected_spoken, split):
    """Retain the whole article while positioning a tail-only audio job."""
    full = ' '.join(full_spoken.split())
    selected = ' '.join(selected_spoken.split())
    audio = split(selected)
    if not selected or selected == full:
        return audio, audio, 0
    start = len(full)-len(selected) if full.endswith(selected) else full.find(selected)
    if start >= 0:
        before = split(full[:start])
        after = split(full[start+len(selected):])
        return before+audio+after, audio, len(before)
    # Cleanup can deliberately omit selected references from the full reading.
    # Keep the complete article visible and put the explicitly requested text
    # after it rather than silently throwing the earlier article away.
    before = split(full)
    return before+audio, audio, len(before)
