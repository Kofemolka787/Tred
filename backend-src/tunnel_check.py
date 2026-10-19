"""Bounded public readiness checks and allowlisted, credential-free diagnostics."""
import gzip
import io
import json
import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*_args,**_kwargs):
        return None


def quick_url(line):
    for found in re.findall(r"https://([a-z0-9-]{1,63})\.trycloudflare\.com",line):
        if found not in {"api","www","dash","developers","support"}:
            return "https://"+found+".trycloudflare.com"
    return None


def tunnel_event(line):
    value=line.lower()
    if "registered tunnel connection" in value:
        return "CONNECTED"
    if "x509:" in value or "certificate verify failed" in value:
        return "TLS_VERIFICATION_FAILED"
    if "no such host" in value or "failed to resolve" in value:
        return "DNS_LOOKUP_FAILED"
    if any(x in value for x in ("failed to dial","failed to serve tunnel","timeout: no recent network activity","dial tcp","preflight connectivity checks failed")):
        return "EDGE_CONNECTION_FAILED"
    if "failed to connect" in value or "unable to reach" in value:
        return "CONNECTION_FAILED"
    return None


def public_probe(url,*,opener=None,timeout=4):
    # Uses the inherited proxy, default CA trust and verified TLS. Redirects are
    # not followed to an unrelated access/login page; no auth headers are sent.
    client=build_opener(NoRedirect()) if opener is None else opener
    request=Request(url+"/healthz",headers={"User-Agent":"TRED-Setup","Cache-Control":"no-cache","Accept-Encoding":"identity"})
    try:
        with client.open(request,timeout=timeout) as response:
            payload=response.read(4097)
            if len(payload)>4096:
                return "UNEXPECTED_RESPONSE"
            if response.headers.get("Content-Encoding","").lower()=="gzip" or payload[:2]==b"\x1f\x8b":
                with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                    payload=compressed.read(4097)
                if len(payload)>4096:
                    return "UNEXPECTED_RESPONSE"
            data=json.loads(payload)
            if isinstance(data,dict) and data.get("ok") is True and data.get("service")=="tred-backend" and data.get("api_version")==1:
                return "OK"
            return "UNEXPECTED_RESPONSE"
    except HTTPError as error:
        status="HTTP_"+str(error.code)
        try:
            with error:
                body=error.read(4096).decode("ascii",errors="ignore")
        except (OSError,TimeoutError):
            return status
        match=re.search(r"(?:error code:\s*|Error\s+)([0-9]{3,5})",body)
        return status+("_CF_"+match[1] if match else "")
    except URLError as error:
        if isinstance(error.reason,ssl.SSLCertVerificationError):
            return "TLS_VERIFICATION_FAILED"
        if isinstance(error.reason,socket.gaierror):
            return "DNS_LOOKUP_FAILED"
        if isinstance(error.reason,TimeoutError):
            return "TIMEOUT"
        return "CONNECTION_FAILED"
    except ssl.SSLCertVerificationError:
        return "TLS_VERIFICATION_FAILED"
    except TimeoutError:
        return "TIMEOUT"
    except (OSError,ValueError,EOFError):
        return "UNEXPECTED_RESPONSE"


def tunnel_command(executable,port):
    return [str(executable),"tunnel","--url",f"http://127.0.0.1:{port}","--protocol","http2","--no-autoupdate","--loglevel","info","--metrics","127.0.0.1:0"]
