"""Bounded, cancellable article downloads, including large interactive pages."""
import re
from threading import Event
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_PAGE_BYTES = 64 * 1024 * 1024


def read_html(response, cancel: Event, limit: int = MAX_PAGE_BYTES) -> bytes:
    data = bytearray()
    while not cancel.is_set():
        block = response.read(min(65536, limit + 1 - len(data)))
        if not block:
            return bytes(data)
        data.extend(block)
        if len(data) > limit:
            raise ValueError(f'Page exceeds the {limit // (1024 * 1024)} MiB download limit. Copy the desired text instead.')
    raise InterruptedError('Page loading cancelled')


def readable_blocks(root, mark_headings=False) -> str:
    """Keep inline spans/links inline, and paragraphs/headings as real blocks."""
    blocks = {'p', 'div', 'section', 'article', 'main', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol', 'blockquote', 'pre', 'figure', 'figcaption', 'table', 'tr'}
    pieces = []
    def visit(node):
        tag = node.tag.lower() if isinstance(node.tag, str) else ''
        if not tag:
            return
        if tag in blocks:
            pieces.append('\n\n')
        if mark_headings and re.fullmatch(r'h[1-6]', tag):
            pieces.append('#' * int(tag[1]) + ' ')
        if tag == 'br':
            pieces.append('\n')
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                pieces.append(child.tail)
        if tag in ('td', 'th'):
            pieces.append('; ')
        if tag in blocks:
            pieces.append('\n\n')
    visit(root)
    text = re.sub(r'[^\S\n]+', ' ', ''.join(pieces))
    text = '\n'.join(line.strip() for line in text.splitlines())
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def wikipedia_article(tree):
    token = lambda name: "contains(concat(' ', normalize-space(@class), ' '), ' " + name + " ')"
    bodies = tree.xpath('//*[' + token('mw-parser-output') + ']')
    if not bodies:
        return None
    body = max(bodies, key=lambda node: len(node.text_content()))
    clutter = ('infobox', 'navbox', 'vertical-navbox', 'metadata', 'ambox', 'hatnote', 'toc', 'mw-editsection', 'reference', 'reflist', 'sistersitebox', 'printfooter', 'catlinks', 'shortdescription', 'IPA', 'mw-empty-elt')
    selector = './/table|.//figure|.//style|.//script|.//sup[' + token('reference') + ']|.//*[' + ' or '.join(token(name) for name in clutter) + ']'
    for element in body.xpath(selector):
        if element.getparent() is not None:
            element.drop_tree()
    # Modern MediaWiki wraps each heading in its own div; older markup uses
    # bare h2/h3 nodes. Prune only explicitly non-prose sections at their level.
    excluded = {'references', 'notes', 'citations', 'sources', 'bibliography', 'further reading', 'external links', 'see also', 'works', 'selected works', 'publications'}
    for heading in list(body.xpath('.//h2|.//h3|.//h4')):
        if heading.getparent() is None:
            continue
        title = ' '.join(heading.text_content().split()).lower().replace('[edit]', '').strip()
        if title not in excluded:
            continue
        level = int(heading.tag[1])
        node = heading.getparent() if 'mw-heading' in heading.getparent().get('class', '').split() else heading
        parent = node.getparent()
        if parent is None:
            continue
        following = node.getnext()
        parent.remove(node)
        while following is not None:
            next_node = following.getnext()
            candidates = [following] if re.fullmatch(r'h[1-6]', str(following.tag)) else following.xpath('.//h2|.//h3|.//h4')
            if any(int(h.tag[1]) <= level for h in candidates):
                break
            parent.remove(following)
            following = next_node
    text = readable_blocks(body, mark_headings=True)
    titles = tree.xpath('//*[@id="firstHeading"]|//h1')
    if titles:
        title = ' '.join(titles[0].text_content().split())
        text = title + '\n\n' + text
    text = re.sub(r'\(\s*;\s*', '(', text)
    return text if text.strip() else None


def extract_article(data: bytes) -> str:
    import trafilatura
    from lxml import html
    tree = html.fromstring(data)
    for element in tree.xpath('//script|//style|//svg|//noscript|//template|//nav|//button'):
        if element.getparent() is not None:
            element.drop_tree()
    wiki = wikipedia_article(tree)
    if wiki:
        return wiki
    # Keep React streaming containers: their hidden="" content may be the actual
    # article, intended to be moved into place by scripts after page load.
    # Explicit article-body hints beat whole-page heuristics on forum layouts.
    # Class-token matches avoid mistaking headers or comment bodies for the post.
    token = lambda name: "contains(concat(' ', normalize-space(@class), ' '), ' " + name + " ')"
    selectors = [
        '//*[' + token('PostsPage-postContent') + ']',
        '//*[@itemprop="articleBody"]',
        '//*[' + token('instapaper_body') + ']',
    ]
    for selector in selectors:
        candidates = tree.xpath(selector)
        if not candidates:
            continue
        body = max(candidates, key=lambda node: len(node.text_content()))
        text = readable_blocks(body, mark_headings=True)
        if len(text) < 80:
            continue
        titles = tree.xpath('//h1[' + token('PostsPageTitle-root') + ']')
        if not titles:
            titles = [node for node in tree.xpath('//h1') if node.text_content().strip() and not node.text_content().strip().isdigit()]
        if titles:
            title = re.sub(r'\s+', ' ', titles[0].text_content()).strip()
            if title and not re.sub(r'\s+', ' ', text).startswith(title):
                text = title + '\n\n' + text
        return text
    text = trafilatura.extract(tree, include_comments=False, include_tables=True)
    if not text:
        raise ValueError('No article text found. Copy text from the browser instead.')
    return text


def load_article(url: str, cancel: Event) -> str:
    if urlparse(url).scheme not in ('http', 'https'):
        raise ValueError('Enter an http:// or https:// webpage URL.')
    request = Request(url, headers={'User-Agent': 'Runespeak-Lite/0.1'})
    with urlopen(request, timeout=20) as response:
        if urlparse(response.url).scheme not in ('http', 'https'):
            raise ValueError('Unsupported redirect')
        if 'html' not in response.headers.get('Content-Type', '').lower():
            raise ValueError('This URL did not return an HTML webpage.')
        data = read_html(response, cancel)
    if cancel.is_set():
        raise InterruptedError('Page loading cancelled')
    return extract_article(data)
