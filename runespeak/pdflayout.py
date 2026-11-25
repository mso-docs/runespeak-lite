"""Readable text from PDFs with PDFium, rebuilt from positioned text runs.

PDFium's plain text keeps every printed line break, puts sub- and superscripts on lines of
their own, and includes page numbers, running heads, table-of-contents leaders, plot tick
labels and table cells. Read aloud, that melds headings into sentences and adds long,
meaningless passages. This module rebuilds lines from text-run boxes, then paragraphs from
spacing, font size and list markers, and drops layout furniture:

* lines are rebuilt from runs that share a baseline band, so sub/superscripts stay inline;
* two-column pages are read column by column;
* line-end hyphenation is undone, words are never glued across line ends;
* headings become Markdown headings (so heading navigation and reference skipping work);
* page numbers, repeated running heads/footers, dot-leader contents pages, plot tick labels,
  display-equation fragments and table bodies are removed (tables are announced).
"""
from collections import Counter
from dataclasses import dataclass, field
import re
import statistics

LIGATURES = {'\ufb00': 'ff', '\ufb01': 'fi', '\ufb02': 'fl', '\ufb03': 'ffi', '\ufb04': 'ffl', '\ufb05': 'st', '\ufb06': 'st'}
SOFT_HYPHENS = ('\x02', '\u00ad')
DOT_LEADER = re.compile(r'(?:\.\s?){4,}\s*\d*\s*$')
LIST_START = re.compile(r'^(?:\d{1,2}[.)]|[•◦▪‣∙·*–-]|\([a-z0-9]{1,3}\)|[a-z][.)])\s')
NUMBERED_HEADING = re.compile(r'^(?:\d+(?:\.\d+){0,3}|[A-Z](?:\.\d+){0,2})\.?\s+\S')
TABLE_NOTICE = 'A table or equation is skipped here.'
CONTENTS_ENTRY = re.compile(r'^(?:[A-Z]?\d+(?:\.\d+)*\.?\s+)?\D{2,90}?\s\d{1,3}$')


@dataclass
class Run:
    left: float
    bottom: float
    right: float
    top: float
    text: str
    size: float

    @property
    def middle(self):
        return (self.bottom + self.top) / 2


@dataclass
class Line:
    runs: list = field(default_factory=list)

    @property
    def left(self):
        return min(run.left for run in self.runs)

    @property
    def right(self):
        return max(run.right for run in self.runs)

    @property
    def top(self):
        return max(run.top for run in self.runs)

    @property
    def bottom(self):
        return min(run.bottom for run in self.runs)

    @property
    def size(self):
        # The size of most of the characters, so a subscript never decides the line's size.
        weights = Counter()
        for run in self.runs:
            weights[round(run.size, 1)] += len(run.text.strip()) or 1
        return weights.most_common(1)[0][0]

    def text(self):
        parts = []
        previous = None
        for run in sorted(self.runs, key=lambda item: item.left):
            piece = run.text
            if previous is not None and parts:
                gap = run.left - previous.right
                joined = parts[-1].endswith((' ', '\n')) or piece.startswith(' ')
                if not joined and gap > 0.18 * max(run.size, previous.size):
                    parts.append(' ')
            parts.append(piece)
            previous = run
        return re.sub(r'[ \t]+', ' ', ''.join(parts)).strip()

    def gaps(self):
        runs = sorted(self.runs, key=lambda item: item.left)
        return [b.left - a.right for a, b in zip(runs, runs[1:])]


def normalize(text):
    for ligature, letters in LIGATURES.items():
        text = text.replace(ligature, letters)
    text = text.replace('\u2212', '-').replace('\r\n', '\n').replace('\r', '\n')
    # Private-use glyphs and variation selectors have no spoken form.
    return re.sub(r'[\ue000-\uf8ff\ufe00-\ufe0f\u200b\ufeff]', '', text)


def prose_sentence(text):
    return bool(re.search(r'[.!?]["”’)]*$', text)) and len(text.split()) > 6


def letters(text):
    return sum(character.isalpha() for character in text)


def prose_like(text):
    """True for lines that read as words, not tick labels or equation fragments."""
    stripped = text.replace(' ', '')
    if not stripped:
        return False
    words = re.findall(r'[^\W\d_]{2,}', text)
    return letters(stripped) >= 3 and (letters(stripped) / len(stripped) >= 0.45 or len(words) >= 3)


def page_runs(textpage):
    runs = []
    for index in range(textpage.count_rects()):
        left, bottom, right, top = textpage.get_rect(index)
        text = normalize(textpage.get_text_bounded(left, bottom, right, top))
        if not text.strip():
            continue
        size = abs(top - bottom)
        try:
            import pypdfium2.raw as raw
            char = textpage.get_index((left + right) / 2, (bottom + top) / 2, max(1.0, (right - left) / 2), max(1.0, (top - bottom) / 2))
            if char is not None and char >= 0:
                size = raw.FPDFText_GetFontSize(textpage.raw, char) or size
        except Exception:
            pass
        runs.append(Run(left, bottom, right, top, text.replace('\n', ' '), float(size)))
    return runs


def build_lines(runs):
    lines = []
    for run in sorted(runs, key=lambda item: (-item.top, item.left)):
        best = None
        for line in lines[-6:]:
            overlap = min(line.top, run.top) - max(line.bottom, run.bottom)
            height = min(line.top - line.bottom, run.top - run.bottom) or 1
            clear = all(run.left >= other.right - 1.5 or run.right <= other.left + 1.5 for other in line.runs)
            if overlap >= 0.45 * height and clear:
                best = line
        if best is None:
            lines.append(Line([run]))
        else:
            best.runs.append(run)
    return sorted(lines, key=lambda line: (-line.top, line.left))


def column_order(lines, width):
    """Read two-column pages column by column; full-width lines stay in place."""
    middle = width / 2
    left = [line for line in lines if line.right <= middle + 4]
    right = [line for line in lines if line.left >= middle - 4]
    if len(left) < 8 or len(right) < 8 or not any(line.left > middle for line in right):
        return lines
    full = [line for line in lines if line not in left and line not in right]
    column_top = max(line.top for line in left + right)
    column_bottom = min(line.bottom for line in left + right)
    above = [line for line in full if line.bottom >= column_top - 2]
    below = [line for line in full if line not in above]
    key = lambda line: (-line.top, line.left)
    return sorted(above, key=key) + sorted(left, key=key) + sorted(right, key=key) + sorted(below, key=key)


def furniture_key(text):
    return re.sub(r'\d+', '#', text.strip().lower())


def table_row(line):
    size = line.size or 10
    wide = [gap for gap in line.gaps() if gap > 1.1 * size]
    return len(wide) >= 2 or (len(wide) == 1 and len(line.text()) < 70 and not line.text().endswith('.'))


@dataclass
class Block:
    kind: str  # 'heading', 'paragraph' or 'table'
    lines: list


def page_blocks(lines, body_size, width):
    """Group cleaned lines into headings, paragraphs and tables."""
    blocks = []
    pitches = [a.bottom - b.bottom for a, b in zip(lines, lines[1:]) if 0 < a.bottom - b.bottom < 3 * body_size]
    pitch = statistics.median(pitches) if pitches else body_size * 1.2
    text_right = max((line.right for line in lines), default=width)
    index = 0
    while index < len(lines):
        line = lines[index]
        text = line.text()
        # Tables: three or more consecutive rows of separated cells.
        run = index
        while run < len(lines) and table_row(lines[run]):
            run += 1
        if run - index >= 3:
            blocks.append(Block('table', lines[index:run]))
            index = run
            continue
        larger = line.size >= body_size + 0.9
        if larger and blocks and blocks[-1].kind == 'heading' and abs(blocks[-1].lines[-1].size - line.size) < .3 \
                and blocks[-1].lines[-1].bottom - line.top < pitch:
            blocks[-1].lines.append(line)  # A heading wrapped onto more lines.
            index += 1
            continue
        short = len(text) < 90 and not re.search(r'[.!:;,]$', text)
        isolated = index + 1 >= len(lines) or lines[index].bottom - lines[index + 1].top > 0.5 * pitch
        numbered = (NUMBERED_HEADING.match(text) and isolated and len(text.split()) <= 12 and not LIST_START.match(text)
                    and re.search(r'[^\W\d_]{3,}', text) and not re.search(r'[=+\[\]<>|]', text))
        if short and (larger or numbered):
            blocks.append(Block('heading', [line]))
            index += 1
            continue
        previous = blocks[-1] if blocks and blocks[-1].kind == 'paragraph' else None
        if previous:
            last = previous.lines[-1]
            gap = last.bottom - line.bottom
            ended = re.search(r'[.!?:]["”’)]*$', last.text()) is not None
            new = (gap > 1.45 * pitch or gap < 0 or LIST_START.match(text)
                   or (ended and last.right < text_right - 0.12 * (text_right - min(l.left for l in lines)))
                   or (ended and line.left > last.left + 0.8 * body_size and not LIST_START.match(last.text()))
                   or abs(line.size - body_size) > 0.9 and abs(last.size - body_size) <= 0.9)
            if not new:
                previous.lines.append(line)
                index += 1
                continue
        blocks.append(Block('paragraph', [line]))
        index += 1
    return blocks


def join_lines(texts):
    result = ''
    for text in texts:
        if not result:
            result = text
        elif result.endswith(SOFT_HYPHENS):
            result = result.rstrip(''.join(SOFT_HYPHENS)) + text
        elif re.search(r'[^\W\d_]-$', result) and text[:1].islower():
            result += text  # Keep real compounds ("pre-registered") without inventing a space.
        else:
            result += ' ' + text
    return re.sub(r'[\x02\u00ad]', '', result)


def heading_level(text):
    match = re.match(r'^(\d+(?:\.\d+)*)\.?\s', text)
    return min(4, match.group(1).count('.') + 1) if match else 1


def pdf_layout_text(pages, cancel=None, progress=lambda message: None):
    """``pages`` is a list of (width, height, runs). Returns narration-ready text."""
    analysed = []
    furniture = Counter()
    all_sizes = Counter()
    for number, (width, height, runs) in enumerate(pages):
        lines = build_lines(runs)
        for line in lines:
            all_sizes[line.size] += len(line.text())
            if line.top > height * 0.9 or line.bottom < height * 0.1:
                furniture[furniture_key(line.text())] += 1
        analysed.append((width, height, lines))
    body_size = all_sizes.most_common(1)[0][0] if all_sizes else 10
    repeated = {key for key, count in furniture.items() if count >= 3 and count >= len(pages) * 0.3}
    output = []
    contents = False
    for number, (width, height, lines) in enumerate(analysed):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Document loading cancelled')
        progress(f'Reorganizing PDF page {number + 1} / {len(analysed)}…')
        texts = [line.text() for line in lines]
        if sum(bool(DOT_LEADER.search(text)) for text in texts) >= 4:
            contents = True
            continue  # A table of contents page.
        kept = []
        spill = contents
        for line, text in zip(lines, texts):
            edge = line.top > height * 0.9 or line.bottom < height * 0.1
            if contents:
                # A contents list that spills onto the next page has no leaders; skip its entries.
                if CONTENTS_ENTRY.match(text) and not prose_sentence(text):
                    continue
                contents = False
            if edge and (re.fullmatch(r'[\divxlc\s.\-–—/]*', text, re.I) or furniture_key(text) in repeated):
                continue  # Page numbers and running heads/footers.
            if DOT_LEADER.search(text):
                continue
            kept.append(line)
        contents = False if spill else contents
        # Plot ticks, axis labels and display-equation pieces: runs of short non-prose lines.
        cleaned = []
        for line in kept:
            text = line.text()
            if prose_like(text) or table_row(line):
                cleaned.append(line)
                continue
            # Numbers that finish an unfinished sentence ("e.g. Säntis +0.0374 [...]") stay;
            # isolated tick labels and equation fragments go.
            previous = cleaned[-1] if cleaned else None
            if (previous is not None and abs(line.size - body_size) <= 0.9 and letters(text) + sum(c.isdigit() for c in text) >= 2
                    and not re.search(r'[.!?:]["”’)]*$', previous.text()) and 0 < previous.bottom - line.bottom < 2.2 * body_size
                    and abs(line.left - previous.left) < 3 * body_size):
                cleaned.append(line)
        for block in page_blocks(cleaned, body_size, width):
            if block.kind == 'table':
                if not output or output[-1] != TABLE_NOTICE:
                    output.append(TABLE_NOTICE)
                continue
            text = join_lines([line.text() for line in block.lines])
            if block.kind == 'heading':
                output.append('#' * heading_level(text) + ' ' + text)
            elif prose_like(text):
                output.append(text)
    return '\n\n'.join(tidy(output))


SENTENCE_END = re.compile(r'[^\W\d_)\]”’"%][.!?:]["”’)]*(?:\s|$)')


def fragment(text):
    """Legend labels, axis titles and table leftovers: no sentence, few words, or mostly codes."""
    if text.startswith('#') or text == TABLE_NOTICE:
        return False
    words = text.split()
    codes = sum(bool(re.fullmatch(r'[A-Z]{2,5}|[-+]?[\d.,%]+|[/()=≥≤<>]', word)) for word in words)
    return (not SENTENCE_END.search(text) and len(words) <= 12) or (len(words) >= 6 and codes / len(words) > 0.4)


def tidy(paragraphs):
    result = []
    for text in paragraphs:
        if fragment(text):
            continue
        if text == TABLE_NOTICE and result and result[-1] == TABLE_NOTICE:
            continue
        result.append(text)
    return result


def pdfium_pages(document, cancel=None, progress=lambda message: None):
    pages = []
    for index in range(len(document)):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Document loading cancelled')
        progress(f'Reading PDF page {index + 1} / {len(document)}…')
        page = document[index]
        textpage = page.get_textpage()
        try:
            width, height = page.get_size()
            pages.append((width, height, page_runs(textpage)))
        finally:
            textpage.close()
            page.close()
    return pages
