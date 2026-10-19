import gzip
import io
import json
import socket
import ssl
import unittest
from urllib.error import HTTPError,URLError

from tunnel_check import NoRedirect,can_wait_for_client,public_probe,quick_url,tunnel_command,tunnel_event


class Response(io.BytesIO):
    def __init__(self,body,headers=None):
        super().__init__(body);self.headers=headers or {}


class Opener:
    def __init__(self,body=None,error=None,headers=None):
        self.body,self.error,self.headers=body,error,headers
    def open(self,request,timeout):
        if request.has_header("Authorization") or request.has_header("X-telegram-init-data"):
            raise AssertionError("Public health probe must not send account credentials")
        if self.error:raise self.error
        return Response(self.body,self.headers)


class TunnelChecks(unittest.TestCase):
    def test_health_requires_actual_tred_api_not_any_json_or_html(self):
        valid={"ok":True,"service":"tred-backend","api_version":1}
        self.assertEqual(public_probe("https://fixture.trycloudflare.com",opener=Opener(json.dumps(valid).encode())),"OK")
        for value in (b"<html>Login</html>",b"[]",b'{}',b'{"service":"tred-backend"}'):
            self.assertEqual(public_probe("https://fixture.trycloudflare.com",opener=Opener(value)),"UNEXPECTED_RESPONSE")

    def test_compressed_health_is_accepted_with_bounded_decompression(self):
        body=json.dumps({"ok":True,"service":"tred-backend","api_version":1}).encode()
        self.assertEqual(public_probe("https://fixture.trycloudflare.com",opener=Opener(gzip.compress(body),headers={"Content-Encoding":"gzip"})),"OK")
        self.assertEqual(public_probe("https://fixture.trycloudflare.com",opener=Opener(gzip.compress(b"x"*10000),headers={"Content-Encoding":"gzip"})),"UNEXPECTED_RESPONSE")
        self.assertEqual(public_probe("https://fixture.trycloudflare.com",opener=Opener(b"\x1f\x8b\x08")),"UNEXPECTED_RESPONSE")

    def test_http_cloudflare_error_code_is_retained_without_response_body(self):
        error=HTTPError("https://fixture.trycloudflare.com",530,"private raw reason",{},io.BytesIO(b'error code: 1033 secret-token-fixture'))
        result=public_probe("https://fixture.trycloudflare.com",opener=Opener(error=error))
        self.assertEqual(result,"HTTP_530_CF_1033")
        self.assertNotIn("secret",result)

    def test_tls_dns_and_timeouts_are_distinguished_without_disabling_tls(self):
        cases=[(ssl.SSLCertVerificationError("private credential fixture"),"TLS_VERIFICATION_FAILED"),(socket.gaierror("private"),"DNS_LOOKUP_FAILED"),(TimeoutError("private"),"TIMEOUT")]
        for reason,expected in cases:
            self.assertEqual(public_probe("https://fixture.trycloudflare.com",opener=Opener(error=URLError(reason))),expected)
        self.assertIsNone(NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_api_hostname_is_not_confused_with_the_assigned_public_address(self):
        self.assertIsNone(quick_url("Request https://api.trycloudflare.com"))
        self.assertEqual(quick_url("INF | https://real-quick-fixture.trycloudflare.com |"),"https://real-quick-fixture.trycloudflare.com")

    def test_transport_command_selects_supported_http2_and_local_metrics(self):
        command=tunnel_command("cloudflared.exe",8790)
        self.assertEqual(command[command.index("--protocol")+1],"http2")
        self.assertEqual(command[command.index("--metrics")+1],"127.0.0.1:0")
        self.assertNotIn("--no-tls-verify",command)
        self.assertNotIn("--no-prechecks",command)

    def test_logs_are_reduced_to_known_categories(self):
        self.assertEqual(tunnel_event("ERR failed to dial edge secret-token-fixture"),"EDGE_CONNECTION_FAILED")
        self.assertEqual(tunnel_event("INF Registered tunnel connection protocol=http2"),"CONNECTED")
        self.assertIsNone(tunnel_event("unrecognized secret-token-fixture"))

    def test_only_local_dns_with_connected_edge_allows_client_verification(self):
        self.assertTrue(can_wait_for_client("DNS_LOOKUP_FAILED","CONNECTED"))
        for status in ("TLS_VERIFICATION_FAILED","UNEXPECTED_RESPONSE","HTTP_530_CF_1033","TIMEOUT","OK"):
            self.assertFalse(can_wait_for_client(status,"CONNECTED"))
        for channel in ("WAITING","DNS_LOOKUP_FAILED","EDGE_CONNECTION_FAILED"):
            self.assertFalse(can_wait_for_client("DNS_LOOKUP_FAILED",channel))


if __name__=="__main__":unittest.main()
