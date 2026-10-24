"""Browser document notices must not become route or weather clicks."""
import unittest
from unittest.mock import patch
import map_component


class DocumentNoticeTests(unittest.TestCase):
    def test_acknowledgements_are_bounded_and_validate_hashes(self):
        state={};one='a'*64;two='b'*64
        with patch.object(map_component.st,'session_state',state):
            self.assertTrue(map_component.document_notice({'document_cache':[one,two]},one))
            self.assertEqual(state,{'_browser_map_documents':{one,two}})
            for invalid in (None,'oops',['x'],[one]*5,[None]):
                self.assertTrue(map_component.document_notice({'document_cache':invalid},one))
                self.assertEqual(state,{'_browser_map_documents':{one,two}})
            map_component.document_notice({'document_cache':[]},one)
            self.assertEqual(state['_browser_map_documents'],set())

    def test_cache_miss_is_scoped_to_current_document_and_clicks_pass_through(self):
        state={};one='a'*64;two='b'*64
        with patch.object(map_component.st,'session_state',state):
            self.assertTrue(map_component.document_notice({'document_request':two},one))
            self.assertEqual(state,{})
            self.assertTrue(map_component.document_notice({'document_request':one},one))
            self.assertEqual(state,{'_map_document_requested:'+one:True})
            for event in (None,{}, {'last_clicked':{'lat':52.,'lng':-3.}},
                          {'last_object_clicked':{'duo':{'kind':'task_sync'}}}):
                self.assertFalse(map_component.document_notice(event,one))
