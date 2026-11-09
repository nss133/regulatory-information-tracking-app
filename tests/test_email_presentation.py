import unittest
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader

from briefing.presentation import compact_attachments
from briefing.render import RenderAttachment


class TestEmailPresentation(unittest.TestCase):
    def test_attachment_roles_and_duplicate_names_preserve_sources(self):
        attachments = [
            RenderAttachment('261105(보도자료) 긴 이름.pdf', 'https://example.com/1'),
            RenderAttachment('261105[별첨1] 모두발언.hwpx', 'https://example.com/2'),
            RenderAttachment('다른 자료.pdf', 'https://example.com/3'),
            RenderAttachment('추가 자료.pdf', 'https://example.com/4'),
            RenderAttachment('문서뷰어', 'https://example.com/view'),
        ]
        result = compact_attachments(attachments)
        self.assertEqual([x['display'] for x in result],
                         ['보도자료 PDF', '별첨1 HWPX', 'PDF', 'PDF 2', '문서뷰어'])
        self.assertEqual([x['url'] for x in result], [x.url for x in attachments])
        self.assertEqual([x['label'] for x in result], [x.label for x in attachments])

    def test_parent_and_child_summary_and_attachment_text_are_escaped(self):
        root = Path(__file__).resolve().parents[1]
        env = Environment(loader=FileSystemLoader(root / 'templates'), autoescape=True)
        env.filters['compact_attachments'] = compact_attachments
        common = dict(item_id='a', is_updated=False, source_name_ko='기관',
                      category_label='보도자료', published_at='2026-11-09',
                      company_flag=False, deadlines=[], reason='', importance='high',
                      title='<안내>', url='https://example.com/detail',
                      summary='<script>bad()</script> / 두 번째 설명',
                      attachments=[RenderAttachment('보도자료 <원문>.pdf', 'https://example.com/a?x=1&y=2')])
        child = dict(common, child_items=[])
        parent = dict(common, child_items=[child])
        html = env.get_template('email.html.j2').render(
            subject='검증', run_date='2026-11-09', sections=[SimpleNamespace(label='high', items=[parent])])
        soup = BeautifulSoup(html, 'html.parser')
        self.assertFalse(soup.select('script'))
        self.assertEqual(len(soup.select('.item-summary br')), 2)
        for a in soup.select('.item-attach a'):
            self.assertEqual(a['href'], common['attachments'][0].url)
            self.assertEqual(a['title'], common['attachments'][0].label)
            self.assertEqual(a.get_text(), '보도자료 PDF')


if __name__ == '__main__':
    unittest.main()
