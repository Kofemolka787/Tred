"""Completed SSE replies, interrupted streams and sanitized transport errors."""
import io
import json
import socket
import ssl
import tempfile
import unittest
from urllib.error import URLError

from ai_market import AIError, OpenAIAnalyzer
from ai_response import ResponseError, read_response
from configure_ai import save


class SSE(io.BytesIO):
    headers={'Content-Type':'text/event-stream; charset=utf-8'}


def frame(value):
    return ('data: '+json.dumps(value)+'\n\n').encode()


class AIResponseTests(unittest.TestCase):
    def test_stream_uses_only_completed_response_not_reasoning_or_partial_text(self):
        done={'status':'completed','output':[{'content':[{'type':'output_text','text':'{"ok":true}'}]}]}
        raw=b': heartbeat\n\n'+frame({'type':'response.reasoning_text.delta','delta':'fixture-private-reasoning'})
        raw+=frame({'type':'response.output_text.delta','delta':'{"unsafe_partial":true}'})
        raw+=frame({'type':'response.completed','response':done})
        self.assertEqual(read_response(SSE(raw),deadline=1,clock=lambda:0),done)

    def test_json_response_remains_compatible(self):
        self.assertEqual(read_response(io.BytesIO(b'{"status":"completed"}'),deadline=1,clock=lambda:0),{'status':'completed'})

    def test_incomplete_error_eof_and_done_cannot_become_a_decision(self):
        cases=[frame({'type':'response.output_text.delta','delta':'{"action":"enter"}'}),
               frame({'type':'response.incomplete','response':{'message':'fixture-secret'}}),
               frame({'type':'error','message':'fixture-secret'}),b'data: [DONE]\n\n']
        for raw in cases:
            with self.subTest(raw=raw[:35]),self.assertRaises(ResponseError) as caught:
                read_response(SSE(raw),deadline=1,clock=lambda:0)
            self.assertNotIn('fixture-secret',str(caught.exception))

    def test_oversize_and_deadline_are_bounded_even_when_a_final_event_arrives_late(self):
        with self.assertRaises(ResponseError):read_response(SSE(b'data: '+b'x'*262145+b'\n'),deadline=1,clock=lambda:0)
        current=[0]
        class Late(SSE):
            def readline(self,*args):
                current[0]=2
                return super().readline(*args)
        with self.assertRaises(ResponseError):
            read_response(Late(frame({'type':'response.completed','response':{}})),deadline=1,clock=lambda:current[0])

    def test_transport_failures_are_distinct_and_no_paid_request_is_retried(self):
        cases=[(TimeoutError('fixture-secret'),'provider_timeout'),
               (URLError(socket.gaierror('fixture-secret')),'provider_dns'),
               (URLError(ssl.SSLError('fixture-secret')),'provider_tls'),
               (URLError(ConnectionResetError('fixture-secret')),'provider_connection')]
        with tempfile.TemporaryDirectory() as tmp:
            save(tmp,'lr_fixture_key_not_real_for_transport',allowed_users=[101],provider='lunaroute')
            for error,code in cases:
                calls=[]
                def opener(request,timeout):calls.append(request);raise error
                ai=OpenAIAnalyzer(tmp,opener=opener)
                self.addCleanup(ai.executor.shutdown)
                with self.subTest(code=code),self.assertRaises(AIError) as caught:ai.review({'pairs':[],'qualified':[]})
                self.assertEqual(caught.exception.code,code);self.assertEqual(len(calls),1)
                self.assertNotIn('fixture-secret',str(caught.exception));self.assertIn('LunaRoute',str(caught.exception))


if __name__=='__main__':unittest.main()
