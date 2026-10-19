"""Exercise route startup/cleanup with controlled process and network fixtures."""
from contextlib import ExitStack,redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import free_start


class Process:
    def __init__(self,lines=""):
        self.stdout=io.StringIO(lines)
        self.code=None
        self.terminated=False
    def poll(self):return self.code
    def terminate(self):self.terminated=True;self.code=0
    def wait(self,timeout=None):return self.code


class LauncherTests(unittest.TestCase):
    def run_fixture(self,status,channel="CONNECTED",recover=False):
        temporary=self.enterContext(tempfile.TemporaryDirectory())
        data=Path(temporary)
        (data/"server-url.txt").write_text("https://old-route.trycloudflare.com\n")
        url="https://fixture-route.trycloudflare.com"
        lines=url+"\n"+("INF Registered tunnel connection protocol=http2\n" if channel=="CONNECTED" else "")
        backend,tunnel=Process(),Process(lines)
        output=io.StringIO()
        calls=0
        def probe(_url):
            nonlocal calls
            calls+=1
            if calls>18:
                pending=json.loads((data/"tunnel-diagnostics.json").read_text())
                self.assertEqual(pending["stage"],"waiting_for_client_dns")
                self.assertFalse((data/"server-url.txt").exists())
                self.assertFalse(backend.terminated or tunnel.terminated)
                if recover:
                    # Simulate a disconnect just after a successful public probe;
                    # the ready path must persist the URL before detecting it.
                    backend.poll=lambda:7 if (data/"server-url.txt").exists() else None
                    return "OK"
                raise KeyboardInterrupt()
            return status
        with ExitStack() as stack:
            stack.enter_context(patch("sys.argv",["free_start.py","--data-dir",str(data)]))
            stack.enter_context(patch.object(free_start,"check_local",side_effect=[False,True]))
            stack.enter_context(patch.object(free_start,"tunnel_binary",return_value=Path("fixture-cloudflared")))
            stack.enter_context(patch.object(free_start.subprocess,"Popen",side_effect=[backend,tunnel]))
            stack.enter_context(patch.object(free_start.time,"sleep"))
            stack.enter_context(patch.object(free_start,"public_probe",side_effect=probe))
            stack.enter_context(redirect_stdout(output))
            with self.assertRaises((KeyboardInterrupt,ValueError)):
                free_start.main()
        return data,backend,tunnel,output.getvalue(),calls,url

    def test_connected_dns_failure_waits_for_phone_without_claiming_verified_route(self):
        data,backend,tunnel,output,calls,url=self.run_fixture("DNS_LOOKUP_FAILED")
        self.assertEqual(calls,19)
        self.assertIn(url,output)
        self.assertIn("доступ пока НЕ подтверждён",output)
        self.assertNotIn("В кабинете TRED нажми",output)
        self.assertFalse((data/"server-url.txt").exists())
        self.assertEqual(json.loads((data/"tunnel-diagnostics.json").read_text())["stage"],"waiting_for_client_dns")
        self.assertTrue(backend.terminated and tunnel.terminated)

    def test_tls_failure_stops_own_processes_and_exposes_no_candidate_url(self):
        data,backend,tunnel,output,calls,url=self.run_fixture("TLS_VERIFICATION_FAILED")
        self.assertEqual(calls,18)
        self.assertNotIn(url,output)
        self.assertFalse((data/"server-url.txt").exists())
        self.assertTrue(backend.terminated and tunnel.terminated)

    def test_dns_failure_without_connected_edge_is_not_kept_running(self):
        data,backend,tunnel,output,calls,url=self.run_fixture("DNS_LOOKUP_FAILED",channel="WAITING")
        self.assertEqual(calls,18)
        self.assertNotIn(url,output)
        self.assertFalse((data/"server-url.txt").exists())
        self.assertTrue(backend.terminated and tunnel.terminated)

    def test_dns_recovery_runs_normal_verified_ready_path(self):
        data,backend,tunnel,output,calls,url=self.run_fixture("DNS_LOOKUP_FAILED",recover=True)
        self.assertEqual(calls,19)
        self.assertEqual((data/"server-url.txt").read_text().strip(),url)
        self.assertIn("В кабинете TRED нажми",output)
        self.assertTrue(tunnel.terminated)


if __name__=="__main__":unittest.main()
