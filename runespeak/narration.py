"""Conservative, inspectable narration cleanup; the source text stays untouched."""
from dataclasses import dataclass
import re

URL = re.compile(
    r'(?i)\b(?:https?://|www\.)[^\s<>\[\]{}]+'
    r'|\bdoi:\s*10\.\d{4,9}/[^\s<>]+'
    r'|\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?-i:com|org|net|edu|gov|io|ai|co|uk|pub|info|dev)\b(?:/[^\s<>\[\]{}]*)?'
)
BACKMATTER = re.compile(r'^(?:(?:\d+(?:\.\d+)*|[IVX]+)[.)]?\s+)?(?P<kind>references|bibliography|works cited|literature cited|appendices|appendix(?:\s+[A-Z0-9]+)?)(?:\s*[:.\-–—]\s*[^.!?]{0,100})?\s*$', re.I)
RESUME = re.compile(r'^(?:conclusions?|discussion|acknowledg(?:e)?ments?|author contributions|funding|limitations|summary)\s*$', re.I)
YEAR = re.compile(r'\b(?:19|20)\d{2}[a-z]?\b')
AUTHOR_STYLE = re.compile(r'\b[A-Z][\w’\'-]+,\s*(?:[A-Z]\.\s*)+|\bet al\.', re.I)


@dataclass
class Reading:
    text: str
    urls: int = 0
    sections: int = 0
    reference_lists: int = 0
    author_lists: int = 0

    def summary(self):
        changes = []
        if self.urls:
            changes.append(f'{self.urls} URLs replaced')
        if self.sections:
            changes.append(f'{self.sections} reference/appendix sections skipped')
        if self.reference_lists:
            changes.append(f'{self.reference_lists} citation lists skipped')
        if self.author_lists:
            changes.append(f'{self.author_lists} author lists shortened')
        return '; '.join(changes) or 'No narration cleanup needed'


def section_heading(line):
    value = re.sub(r'^\s{0,3}#{1,6}\s+', '', line).strip().strip('*_ ')
    return BACKMATTER.fullmatch(value)


def citation_entry(line):
    # Dates or ordinary numbered/bulleted text alone never qualify.
    value = re.sub(r'^\s*(?:\[\d+\]|\d+[.)]|[-*])\s*', '', line)
    return bool(YEAR.search(value) and AUTHOR_STYLE.search(value) and
                (re.search(r'\b(?:doi|arxiv|proceedings|journal|conference|vol\.)\b', value, re.I) or URL.search(value)))


def shorten_byline(line):
    prefix = re.match(r'^\s*(?:by|authors?)\s*:?\s+', line, re.I)
    value = line[prefix.end():] if prefix else line
    names = re.split(r',\s*|\s+and\s+|\s*&\s*|;\s*', value.strip())
    names = [name.strip() for name in names if name.strip()]
    if len(names) < 7:
        return line
    def looks_like_name(name):
        tokens = name.split()
        return 2 <= len(tokens) <= 5 and all(token[0].isupper() and not any(c.isdigit() for c in token) for token in tokens)
    if not all(looks_like_name(name) for name in names):
        return line
    return ('By ' if prefix else '') + ', '.join(names[:3]) + ', and additional authors.'


def shorten_frontmatter(text):
    """Abbreviate dense author/affiliation rows only before a paper's Abstract."""
    lines = text.splitlines()
    abstract = next((i for i, line in enumerate(lines[:200]) if re.fullmatch(r'(?:#{1,6}\s+)?abstract', line.strip(), re.I)), None)
    if abstract is None:
        return text, 0
    name_token = re.compile(r"^[A-ZÀ-ÖØ-Þ][\w’'\-]*(?:[.*∗†‡,]|\d)*$")
    def name_row(line):
        tokens = line.strip().split()
        return (2 <= len(tokens) <= 20 and len(line) <= 150 and
                all(name_token.fullmatch(token) for token in tokens[:2]) and
                not all(token.isupper() for token in tokens[:2]) and
                not re.search(r'[!?;]', line))
    output = []
    index = 0
    count = 0
    while index < abstract:
        if name_row(lines[index]):
            end = index
            names = []
            while end < abstract and (not lines[end].strip() or name_row(lines[end])):
                if lines[end].strip():
                    words = lines[end].split()[:2]
                    names.append(' '.join(re.sub(r'[.*∗†‡,\d]+$', '', word) for word in words))
                end += 1
            if len(names) >= 7:
                output.append('Authors include ' + ', '.join(names[:3]) + ', and additional authors.\n')
                count += 1
                index = end
                continue
        output.append(lines[index])
        index += 1
    output.extend(lines[abstract:])
    return '\n'.join(output), count


def prepare_reading(text, include_backmatter=False):
    result = Reading('')
    if not include_backmatter:
        text, result.author_lists = shorten_frontmatter(text)
    lines = text.splitlines()
    output = []
    skipping = False
    skipped_heading_level = None
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = section_heading(line)
        if heading and not include_backmatter:
            kind = heading['kind'].lower()
            output.append('There is an appendix here.' if kind.startswith('append') else 'There is a references section here.')
            result.sections += 1
            skipping = True
            markdown = re.match(r'^\s{0,3}(#{1,6})\s', line)
            skipped_heading_level = len(markdown[1]) if markdown else None
            index += 1
            continue
        if skipping:
            markdown = re.match(r'^\s{0,3}(#{1,6})\s', line)
            # Only clear structural evidence resumes reading. A citation's title
            # must not be mistaken for the next article section.
            resume_line = re.sub(r'^\s*(?:\d+(?:\.\d+)*[.)]?\s+)', '', line).strip()
            resume = bool(RESUME.fullmatch(resume_line)) or bool(markdown and skipped_heading_level and len(markdown[1]) <= skipped_heading_level)
            if not resume:
                index += 1
                continue
            skipping = False
        if not include_backmatter and citation_entry(line):
            end = index
            entries = 0
            while end < len(lines):
                if citation_entry(lines[end]):
                    entries += 1
                elif lines[end].strip():
                    break
                end += 1
            if entries >= 3:
                output.append('There is a list of references here.')
                result.reference_lists += 1
                index = end
                continue
        if not include_backmatter:
            shortened = shorten_byline(line)
            if shortened != line:
                result.author_lists += 1
                line = shortened
        output.append(line)
        index += 1
    spoken = '\n'.join(output)
    # Handle user-pasted Markdown too: speak a descriptive label, not its URL.
    spoken = re.sub(r'!?\[([^\]]*)\]\((https?://[^\s)]+)\)', lambda match: match[1] if match[1].strip() and not URL.fullmatch(match[1].strip()) else 'there is a URL here', spoken, flags=re.I)
    def replace_url(match):
        result.urls += 1
        value = match[0]
        ending = re.search(r'[.,;:!?)]+$', value)
        return 'there is a URL here' + (ending[0] if ending else '')
    result.text = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', URL.sub(replace_url, spoken))
    return result
