import unittest
from briefing.sources.na import NaAssemblyConnector

def row(number, stage):
 return '<tr>'+''.join('<td>'+v+'</td>' for v in [number,f'<a data-bill-id="ID{number}" title="보험업법 일부개정법률안">보험업법 일부개정법률안</a>','의원','2026-11-11','','','',stage])+'</tr>'

class StageTransitionTests(unittest.TestCase):
 def test_mixed_transition_response_excludes_early_row(self):
  c=NaAssemblyConnector(None)
  with self.assertLogs('briefing.sources.na',level='WARNING'):
   items=c._parse_sch_html('<table>'+row('1','체계자구심사')+row('2','소관위의결')+'</table>',milestone='체계·자구심사',date_col=None)
  self.assertEqual(len(items),1)
  self.assertIn('ID1',items[0].url)
 def test_wholly_wrong_stage_still_fails_closed(self):
  with self.assertRaises(ValueError):
   NaAssemblyConnector(None)._parse_sch_html('<table>'+row('2','소관위의결')+'</table>',milestone='체계·자구심사',date_col=None)

 def test_approved_systematic_review_is_a_later_stage(self):
  from briefing.legislation import late_stage
  self.assertTrue(late_stage('체계자구의결'))
  items=NaAssemblyConnector(None)._parse_sch_html('<table>'+row('3','체계자구의결')+'</table>',milestone='체계·자구심사',date_col=None)
  self.assertEqual(len(items),1)

 def test_mixed_unfiltered_early_reception_still_fails(self):
  with self.assertRaises(ValueError):
   NaAssemblyConnector(None)._parse_sch_html('<table>'+row('1','체계자구심사')+row('2','소관위접수')+'</table>',milestone='체계·자구심사',date_col=None)
