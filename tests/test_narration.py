import unittest
from runespeak.narration import prepare_reading


class NarrationTests(unittest.TestCase):
    def test_raw_urls_are_replaced_and_sentence_punctuation_survives(self):
        result = prepare_reading('See https://example.org/long/path?token=secret. Then www.example.com/report.')
        self.assertEqual(result.text, 'See there is a URL here. Then there is a URL here.')
        self.assertEqual(result.urls, 2)
        self.assertNotIn('secret', result.text)

    def test_references_and_appendix_skip_but_article_body_remains(self):
        source = 'Main argument.\n\nReferences\nSmith, A. (2020). A paper.\nLong citation list.\n\nAppendix A: Proofs\nLong derivation.'
        result = prepare_reading(source)
        self.assertIn('Main argument.', result.text)
        self.assertIn('There is a references section here.', result.text)
        self.assertIn('There is an appendix here.', result.text)
        self.assertNotIn('Long citation', result.text)
        self.assertNotIn('Long derivation', result.text)
        self.assertEqual(result.sections, 2)

    def test_resumes_at_next_markdown_section(self):
        source = '## References\nCitation details\n### A citation subheading\nStill references\n## Discussion\nImportant discussion.'
        result = prepare_reading(source)
        self.assertNotIn('Still references', result.text)
        self.assertIn('Important discussion.', result.text)

    def test_ordinary_lists_and_reference_mentions_remain(self):
        source = 'References matter in science.\n\n- Gather evidence.\n- Run experiments.\n- Compare results.\n\n1. In 2020, improve safety.\n2. In 2021, publish results.\n3. In 2022, repeat the experiment.'
        self.assertEqual(prepare_reading(source).text, source)

    def test_only_dense_citation_runs_without_headings_are_skipped(self):
        citations = '\n'.join(f'[{i}] Smith, A. (202{i}). Paper title. Journal of Science. https://example.org/{i}' for i in range(1, 4))
        result = prepare_reading('Before.\n'+citations+'\nAfter, an important argument.')
        self.assertIn('There is a list of references here.', result.text)
        self.assertNotIn('Paper title', result.text)
        self.assertIn('After, an important argument.', result.text)
        self.assertEqual(result.reference_lists, 1)

    def test_a_single_relevant_citation_is_kept(self):
        source = '[1] Smith, A. (2020). A useful paper. Journal of Science.'
        self.assertEqual(prepare_reading(source).text, source)

    def test_long_author_byline_shortens_without_cutting_normal_prose(self):
        names = 'Alice Smith, Bob Jones, Carol Green, David White, Ellen Brown, Frank Black, Grace Grey'
        result = prepare_reading('By '+names+'\n\nMain article text.')
        self.assertIn('By Alice Smith, Bob Jones, Carol Green, and additional authors.', result.text)
        self.assertIn('Main article text.', result.text)
        self.assertEqual(result.author_lists, 1)
        prose = 'We discuss apples, pears, oranges, berries, plums, grapes, and other important fruit.'
        self.assertEqual(prepare_reading(prose).text, prose)

    def test_full_reading_retains_backmatter_but_still_replaces_urls(self):
        source = 'References\nCitation at https://example.org/paper\nAppendix A\nProof details.'
        result = prepare_reading(source, include_backmatter=True)
        self.assertIn('Proof details.', result.text)
        self.assertIn('Citation at there is a URL here', result.text)
        self.assertEqual(result.sections, 0)

    def test_markdown_descriptive_links_remain_readable(self):
        result = prepare_reading('Read [the article](https://example.org/very-long-path) for details.')
        self.assertEqual(result.text, 'Read the article for details.')

    def test_bare_domains_and_doi_links(self):
        result = prepare_reading('Visit arxiv.org/abs/2507.11473 or doi:10.1000/example. End.')
        self.assertEqual(result.text, 'Visit there is a URL here or there is a URL here. End.')
        self.assertEqual(result.urls, 2)

    def test_multiline_paper_authors_and_affiliations_are_abbreviated(self):
        names = ['Alice Smith', 'Bob Jones', 'Carol Green', 'David White', 'Ellen Brown', 'Frank Black', 'Grace Grey']
        source = 'Paper title\n\n'+'\n'.join(name+' Research Institute' for name in names)+'\n\nAbstract\nImportant abstract content.'
        result = prepare_reading(source)
        self.assertIn('Authors include Alice Smith, Bob Jones, Carol Green, and additional authors.', result.text)
        self.assertNotIn('Research Institute', result.text)
        self.assertIn('Important abstract content.', result.text)
        self.assertEqual(result.author_lists, 1)
        self.assertEqual(prepare_reading(source, True).text, source)

    def test_arxiv_subject_class_is_not_a_bare_url(self):
        self.assertEqual(prepare_reading('arXiv:2507.11473v2 [cs.AI]').text, 'arXiv:2507.11473v2 [cs.AI]')
