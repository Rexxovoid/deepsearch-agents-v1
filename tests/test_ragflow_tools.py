"""Offline RAGFlow protocol regressions; no external model calls."""
import os
import unittest
from unittest.mock import Mock, patch
import requests
from app.tools import ragflow_tools as tools
from app.ragflow.rag_config import _load_ragflow_env


def response(payload=None, lines=None, status=200):
    result = Mock()
    result.status_code = status
    result.headers = {'content-type': 'text/event-stream' if lines is not None else 'application/json'}
    result.json.return_value = payload
    result.iter_lines.return_value = iter(lines or [])
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class RagflowTests(unittest.TestCase):
    def test_config_normalizes_api_suffix_and_rejects_missing_values(self):
        with patch('app.ragflow.rag_config.load_dotenv'), patch.dict(os.environ, {'RAGFLOW_API_KEY':' test-key ', 'RAGFLOW_API_URL':' http://localhost:9380/api/v1/ '}):
            self.assertEqual(_load_ragflow_env(), ('test-key', 'http://localhost:9380'))
        with patch('app.ragflow.rag_config.load_dotenv'), patch.dict(os.environ, {'RAGFLOW_API_KEY':'111'}):
            with self.assertRaises(ValueError): _load_ragflow_env()

    def test_timeout_and_authorization(self):
        with patch.object(tools, '_load_ragflow_env', return_value=('secret','http://ragflow')), patch.object(tools.requests, 'request') as request:
            tools._request('GET','/chats')
            kwargs=request.call_args.kwargs
            self.assertEqual(kwargs['timeout'],(5,60))
            self.assertEqual(kwargs['headers']['Authorization'],'Bearer secret')

    def test_chat_listing_supports_both_server_formats(self):
        for data in ([{'name':'助手'}], {'chats':[{'name':'助手'}],'total':1}):
            with patch.object(tools,'_json_request',return_value=data):
                self.assertIn('助手名称:助手',tools.get_assistant_list.invoke({}))
                self.assertIn('未填写',tools.get_assistant_list.invoke({}))

    def test_http_200_business_error_is_not_an_answer(self):
        stream=response(lines=[': heartbeat','event: message','data:{"code":500,"message":"","data":{"answer":"**ERROR**: "}}'])
        with self.assertRaisesRegex(RuntimeError,'code=500'): tools._read_answer(stream)
        with self.assertRaisesRegex(RuntimeError,'AssertionError'):
            tools._read_answer(response({'code':100,'message':'AssertionError()'}))

    def test_cumulative_sse_and_empty_answer(self):
        stream=response(lines=[':heartbeat','data:{"code":0,"data":{"answer":"你"}}','data:{"code":0,"data":{"answer":"你好"}}','data:{"code":0,"data":true}'])
        self.assertEqual(tools._read_answer(stream),'你好')
        with self.assertRaisesRegex(RuntimeError,'未返回回答'):
            tools._read_answer(response(lines=['data:{"code":0,"data":true}']))

    def test_success_and_old_endpoint_fallback_cleanup(self):
        for fallback in (False,True):
            answer=response(lines=['data:{"code":0,"data":{"answer":"回答"}}','data:[DONE]'])
            replies=[response(status=404),answer] if fallback else [answer]
            with patch.object(tools,'_list_chats',return_value=[{'id':'chat-1','name':'助手'}]), patch.object(tools,'_json_request',side_effect=[{'id':'session-1'},None]) as api, patch.object(tools,'_request',side_effect=replies) as request:
                self.assertEqual(tools.create_ask_delete.invoke({'chat_name':'助手','question':'问题'}),'回答')
                first=request.call_args_list[0]
                self.assertEqual(first.args[1],'/chat/completions')
                payload=first.kwargs['json']
                self.assertEqual(payload['messages'][0]['content'],'问题')
                self.assertTrue(payload['legacy'])
                if fallback:self.assertEqual(request.call_args.args[1],'/chats/chat-1/completions')
                self.assertEqual(api.call_args.args,('DELETE','/chats/chat-1/sessions'))
                self.assertEqual(api.call_args.kwargs['json'],{'ids':['session-1']})

    def test_failure_also_deletes_session(self):
        stream=response(lines=['data:{"code":500,"message":"","data":{"answer":"**ERROR**"}}'])
        with patch.object(tools,'_list_chats',return_value=[{'id':'chat','name':'助手'}]), patch.object(tools,'_json_request',side_effect=[{'id':'session'},None]) as api, patch.object(tools,'_request',return_value=stream) as request:
            result=tools.create_ask_delete.invoke({'chat_name':'助手','question':'问题'})
            self.assertIn('code=500',result)
            self.assertEqual(api.call_args.args[0],'DELETE')
            self.assertEqual(request.call_count,1)

    def test_unknown_assistant_and_timeout_have_clear_errors(self):
        with patch.object(tools,'_list_chats',return_value=[]):
            self.assertIn('没有找到',tools.create_ask_delete.invoke({'chat_name':'不存在','question':'问题'}))
        with patch.object(tools,'_list_chats',side_effect=requests.Timeout()):
            self.assertIn('超时',tools.get_assistant_list.invoke({}))

    def test_encoding_damaged_question_does_not_reach_server(self):
        with patch.object(tools, '_list_chats') as chats:
            result = tools.create_ask_delete.invoke({'chat_name': '助手', 'question': '???!!!'})
            self.assertIn('编码丢失', result)
            chats.assert_not_called()

if __name__=='__main__': unittest.main()
