"""PDF reading without Poppler: lines, paragraphs, headings and furniture (PDFium layout)."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from runespeak.documents import extract_file
from runespeak.narration import prepare_reading
from runespeak.pdflayout import Line, Run, build_lines, join_lines, normalize, prose_like, tidy, TABLE_NOTICE


def escape(text):
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def layout_pdf(pages):
    """A minimal multi-page PDF. ``pages`` holds (x, y, size, text) tuples per page."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', None, b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for runs in pages:
        stream = ' '.join(f'BT /F1 {size} Tf {x} {y} Td ({escape(text)}) Tj ET' for x, y, size, text in runs).encode()
        objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
        content = len(objects)
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {content} 0 R >>'.encode())
        kids.append(f'{len(objects)} 0 R')
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {len(kids)} >>'.encode()
    data = bytearray(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{number} 0 obj\n'.encode() + body + b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    for offset in offsets:
        data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return bytes(data)


def paragraph(x, y, lines, size=10, pitch=12):
    return [(x, y - index * pitch, size, text) for index, text in enumerate(lines)]


def body_page(number, heading, lines, top=700):
    runs = [(72, 760, 8, 'Expanded proposal draft'), (300, 40, 9, str(number)), (72, top, 14, heading)]
    return runs + paragraph(72, top - 24, lines)


# Snippets from the 26-page expanded proposal that read badly before this module existed.
FINDINGS = [
    'Under a controlled prevalence shift, the Bayes correction lowered the Swiss Brier score at',
    'every target away from the source rate; for heavy rain it fell from 0.2436 to 0.1222, close',
    'to the predicted gain. After correction, expected calibration error was at most 0.016 in',
    'every Swiss controlled cell.',
]
MOTIVATION = [
    'Event prevalence is the proportion of examples in which an event occurs within the chosen',
    'prediction horizon. A probability forecast mixes two things: how strongly the current inputs',
    'point toward the event, and how common the event is in general.',
]


class LayoutPdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def read(self, pages):
        path = self.folder / 'paper.pdf'
        path.write_bytes(layout_pdf(pages))
        with patch('runespeak.documents.find_tool', return_value=None):  # No Poppler, as on a fresh Mac.
            return extract_file(path, threading.Event())

    def test_paragraphs_headings_and_page_furniture(self):
        contents = [(72, 700, 14, 'Contents')] + [(72, 680 - 14 * i, 10, f'{i} Section title {"." * 40} {i + 3}') for i in range(1, 7)]
        text = self.read([
            contents,
            body_page(2, '1 Motivation', MOTIVATION),
            body_page(3, '5.1 Findings', FINDINGS),
            body_page(4, '5.2 Next steps', ['Confirm the gate on ten-minute data and report both periods separately.']),
        ])
        self.assertNotIn('Section title', text, 'table of contents is skipped')
        self.assertNotIn('Expanded proposal draft', text, 'running head is removed')
        self.assertNotRegex(text, r'(?m)^\d+$', 'page numbers are removed')
        self.assertIn('# 1 Motivation', text)
        self.assertIn('## 5.1 Findings', text)
        # Lines of one paragraph are joined with spaces; no word is glued to the next line's word.
        self.assertIn('lowered the Swiss Brier score at every target away', text)
        self.assertIn('within the chosen prediction horizon.', text)
        spoken = prepare_reading(text).text
        self.assertIn('Findings.', spoken, 'headings end with a stop so they do not run into the next sentence')
        self.assertNotIn('#', spoken)

    def test_line_end_hyphenation_and_subscripts(self):
        runs = paragraph(72, 650, [
            'The correction multiplies each forecast by a factor learned during the calibra-',
            'tion years, which keeps the evidence term unchanged at every station.',
        ])
        # A subscript sits slightly lower on the same line: it must stay inline.
        runs += [(72, 600, 10, 'The new rate is pi'), (151, 597, 7, 'n'), (157, 600, 10, ' at the target station today.')]
        text = self.read([runs])
        self.assertIn('calibration years', text)
        self.assertIn('pin at the target', text.replace(' n ', 'n '))
        self.assertNotIn('\x02', text)

    def test_plot_tick_labels_are_dropped(self):
        ticks = [(80 + 40 * i, 400, 7, f'10-{i}') for i in range(6)] + [(60, 420 + 20 * i, 7, str(i)) for i in range(5)]
        text = self.read([paragraph(72, 700, MOTIVATION) + ticks + [(72, 360, 9, 'Figure 1: Observed against predicted gain for every station.')]])
        self.assertNotRegex(text, r'10-\d')
        self.assertIn('Figure 1: Observed against predicted gain', text)


class LayoutUnitTests(unittest.TestCase):
    def test_ligatures_minus_and_private_glyphs(self):
        self.assertEqual(normalize('\ufb01rst \ufb02ow \u22120.5\ue156'), 'first flow -0.5')

    def test_join_lines(self):
        self.assertEqual(join_lines(['calibra\x02', 'tion years']), 'calibration years')
        self.assertEqual(join_lines(['a pre-', 'registered test']), 'a pre-registered test')
        self.assertEqual(join_lines(['the end of a line', 'and the next']), 'the end of a line and the next')

    def test_superscript_joins_its_line(self):
        lines = build_lines([Run(65, 545, 220, 555, 'by odds(', 10), Run(268, 547.5, 273, 551.8, 'π', 10),
                             Run(274, 546, 278, 549, 'n', 7), Run(280, 545, 330, 555, ') lower', 10)])
        self.assertEqual(len(lines), 1)
        self.assertIn('πn', lines[0].text())
        self.assertTrue(lines[0].text().startswith('by odds(') and lines[0].text().endswith('lower'))

    def test_prose_and_fragments(self):
        self.assertFalse(prose_like('10 -6 10 -5 10 -4'))
        self.assertTrue(prose_like('Yes when only the rate changes, by almost exactly the predicted amount.'))
        self.assertEqual(tidy(['mean-match -0.0031', TABLE_NOTICE, TABLE_NOTICE, 'A real sentence here.']),
                         [TABLE_NOTICE, 'A real sentence here.'])


if __name__ == '__main__':
    unittest.main()
