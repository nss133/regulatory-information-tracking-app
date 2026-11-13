import unittest
from bs4 import BeautifulSoup
from briefing.kordoc import find_attachment_links

HTML='''<form name="fileList" action="/np/cmm/fms/FileDown.do;jsessionid=PRIVATE" method="post"></form><a href="#LINK" alt="검증한 보도자료.pdf" onclick="javascript:fn_egov_downFile('FILE_123','1','pdf')">다운로드</a>'''
class PipcAttachmentsTests(unittest.TestCase):
 def test_same_origin_file_link_without_session_token(self):
  x=find_attachment_links(BeautifulSoup(HTML,'html.parser'),'https://www.pipc.go.kr/article')
  self.assertEqual(x,[('검증한 보도자료.pdf','https://www.pipc.go.kr/np/cmm/fms/FileDown.do?atchFileId=FILE_123&fileSn=1&fileExtsn=pdf')])
 def test_other_host_and_arbitrary_javascript_are_not_interpreted(self):
  for h in (HTML.replace('/np/cmm/fms/FileDown.do','https://evil.example/FileDown.do'),HTML.replace("fn_egov_downFile(","other_function(")):
   self.assertEqual(find_attachment_links(BeautifulSoup(h,'html.parser'),'https://www.pipc.go.kr/article'),[])
