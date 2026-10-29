import io
import threading
import unittest
from runespeak.webpage import extract_article, read_html


class WebpageTests(unittest.TestCase):
    def test_accepts_page_over_old_five_mb_limit(self):
        payload = b'x' * 6_000_000
        self.assertEqual(read_html(io.BytesIO(payload), threading.Event()), payload)

    def test_rejects_above_limit(self):
        with self.assertRaisesRegex(ValueError, 'download limit'):
            read_html(io.BytesIO(b'x' * 1025), threading.Event(), limit=1024)

    def test_cancelled_download(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            read_html(io.BytesIO(b'content'), cancel)

    def test_exact_limit_is_allowed(self):
        self.assertEqual(read_html(io.BytesIO(b'abcd'), threading.Event(), limit=4), b'abcd')

    def test_embedded_scripts_do_not_become_narration(self):
        article = 'This article explains connections between neurons and meaningful algorithms. ' * 30
        page = f'<html><body><article><p>{article}</p></article><script>{"noise" * 1_100_000}</script></body></html>'
        result = extract_article(page.encode())
        self.assertIn('connections between neurons', result)
        self.assertNotIn('noise', result)

    def test_streamed_forum_body_is_not_lost_inside_hidden_react_container(self):
        page = '''<html><body><nav>AI ALIGNMENT FORUM Login</nav>
        <h1 class="PostsPageTitle-root">Model Organisms of <span>Misalignment</span></h1>
        <div hidden id="S:4"><div class="PostsPage-postContent instapaper_body">
        <p>TL;DR: This is the actual article body, which React will move into the visible page after loading.</p>
        <h2>The Problem</h2><p>Read <a href="/research">research agendas</a> as one paragraph, with <strong>inline emphasis</strong>.</p>
        </div></div><aside>Curated and popular this week Login 124 30</aside></body></html>'''
        text = extract_article(page.encode())
        self.assertTrue(text.startswith('Model Organisms of Misalignment\n\nTL;DR:'))
        self.assertIn('The Problem\n\nRead research agendas as one paragraph, with inline emphasis.', text)
        self.assertNotIn('Login', text)
        self.assertNotIn('Curated and popular', text)

    def test_article_body_preserves_paragraphs_lists_and_inline_names(self):
        page = '''<html><body><header>Login</header><div itemprop="articleBody">
        <p>Written by <a>Alice</a>, <a>Bob</a>, and <a>Carol</a>. Their detailed article starts here with the main argument.</p>
        <ul><li>First useful point.</li><li>Second useful point.</li></ul>
        <p>A final paragraph with <em>important</em> details.</p></div><footer>Comment Subscribe</footer></body></html>'''
        text = extract_article(page.encode())
        self.assertIn('Written by Alice, Bob, and Carol.', text)
        self.assertIn('First useful point.\n\nSecond useful point.', text)
        self.assertNotIn('Subscribe', text)

    def test_empty_explicit_body_does_not_hide_generic_article(self):
        paragraph = 'This is a useful and complete generic article paragraph about local narration. ' * 15
        page = f'<html><body><div class="instapaper_body"></div><article><p>{paragraph}</p></article></body></html>'
        self.assertIn('complete generic article', extract_article(page.encode()))

    def test_wikipedia_keeps_prose_and_headings_not_furniture(self):
        page = '''<html><body><h1 id="firstHeading">Richard Feynman</h1>
        <div class="mw-parser-output"><table class="infobox"><tr><td>Resting place</td></tr></table>
        <div class="hatnote">For other uses see navigation</div>
        <p>Richard Feynman was a theoretical physicist.<sup class="reference">[1]</sup></p>
        <div class="mw-heading mw-heading2"><h2>Early life</h2><span class="mw-editsection">[edit]</span></div>
        <p>He grew up in New York and studied physics.</p><ul><li>A relevant biographical point.</li></ul>
        <div class="mw-heading mw-heading2"><h2>References</h2></div><ol><li>A very long bibliography</li></ol>
        <div class="mw-heading mw-heading2"><h2>External links</h2></div><p>Link directory filler.</p>
        </div></body></html>'''
        text = extract_article(page.encode())
        self.assertIn('theoretical physicist', text)
        self.assertIn('## Early life', text)
        self.assertIn('A relevant biographical point.', text)
        for unwanted in ('Resting place', 'navigation', '[1]', '[edit]', 'bibliography', 'Link directory'):
            self.assertNotIn(unwanted, text)
