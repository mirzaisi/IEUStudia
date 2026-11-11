"""Errors in words for anyone (net.plain_error); sessions that share a few TLS contexts instead of reading the
certificate bundle for every connection; how long a server that doesn't answer may take."""

import errno
import ssl
import sys
import types

import certifi
import pytest
import requests
import urllib3.util.ssl_ as u3

from studia import net
from studia.net import NetworkError, plain_error


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths and messages")
def test_errors_are_said_plainly():
    assert plain_error(requests.ConnectionError("Max retries"), "https://huggingface.co/x").startswith(
        "Can't reach huggingface.co. Check your internet connection")
    assert plain_error(requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED"), "https://learn.ieu.edu.tr/").startswith(
        "A secure connection to learn.ieu.edu.tr was blocked")
    assert plain_error(requests.ReadTimeout("read timed out"), "https://pypi.org/").startswith("pypi.org took too long")
    assert plain_error(OSError(errno.ENOSPC, "No space left on device")).startswith("Your disk is full")
    assert plain_error(MemoryError()).startswith("Not enough memory")
    denied = plain_error(PermissionError(13, "Access is denied", r"C:\Users\x\OneDrive\EEE 215 - Study pack.md"))
    assert denied.startswith("Windows did not let IEUStudia use EEE 215 - Study pack.md") and "Settings" not in denied
    unplugged = plain_error(OSError(None, "The system cannot find the path specified", "Z:\\IEUStudia", 3))
    assert unplugged.startswith("Z:\\IEUStudia can't be reached: is the drive")
    moved = plain_error(FileNotFoundError(2, "No such file or directory", r"C:\Users\x\Videos\Lecture.mp4"))
    assert moved == "Lecture.mp4 isn't there any more: was it moved or deleted?"
    assert plain_error(NetworkError("Can't reach panopto.ieu.edu.tr.")) == "Can't reach panopto.ieu.edu.tr."
    assert plain_error(RuntimeError("expected HTTP 206, got 403")) == "expected HTTP 206, got 403."   # one full stop
    assert plain_error(RuntimeError("Panopto is still processing this recording.")).endswith("recording.")


def _pool(adapter, url, verify=True, cert=None):
    req = requests.Request("GET", url).prepare()
    return adapter.get_connection_with_tls_context(req, verify, proxies={}, cert=cert)


def test_each_session_has_its_own_tls_context_with_the_bundle_read_once(monkeypatch):
    sessions = [net.make_session(), net.make_session(), net.plain_session()]
    monkeypatch.setattr(certifi, "where", lambda: 1 / 0)                     # the bundle isn't read again
    sessions.append(net.make_session())
    used = [s.get_adapter("https://x/").poolmanager.connection_pool_kw["ssl_context"] for s in sessions]
    assert len({id(c) for c in used}) == len(sessions) and all(c in net._ours for c in used)   # a stalled handshake
    ref, ctx = u3.create_urllib3_context(), used[0]                                           # holds up only its own
    for name in ("options", "minimum_version", "maximum_version", "verify_flags", "verify_mode", "check_hostname",
                 "hostname_checks_common_name"):
        assert getattr(ctx, name) == getattr(ref, name), name           # as urllib3 makes them for one connection
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] == certifi.contents().count("BEGIN CERTIFICATE")


def test_a_shared_context_reads_no_bundle_per_connection():
    adapter = net.make_session().get_adapter("https://learn.ieu.edu.tr/")
    pool = _pool(adapter, "https://learn.ieu.edu.tr/student")
    adapter.cert_verify(pool, "https://learn.ieu.edu.tr/student", True, None)
    assert pool.conn_kw["ssl_context"] in net._ours
    assert (pool.cert_reqs, pool.ca_certs, pool.ca_cert_dir) == ("CERT_REQUIRED", None, None)
    conn = pool._new_conn()                                  # what urllib3 connects with (not connected here)
    assert conn.ssl_context is pool.conn_kw["ssl_context"] and not (conn.ca_certs or conn.ca_cert_dir or conn.ca_cert_data)


def test_turning_off_verification_or_another_bundle_goes_requests_own_way(tmp_path):
    adapter = net.plain_session().get_adapter("https://example.com/")
    off = _pool(adapter, "https://example.com/", verify=False)
    adapter.cert_verify(off, "https://example.com/", False, None)
    assert "ssl_context" not in off.conn_kw and off.cert_reqs == "CERT_NONE"   # the shared one would be changed
    bundle = tmp_path / "company.pem"
    bundle.write_text("")
    own = _pool(adapter, "https://example.com/", verify=str(bundle))
    adapter.cert_verify(own, "https://example.com/", str(bundle), None)
    assert "ssl_context" not in own.conn_kw and own.ca_certs == str(bundle)
    proxied = types.SimpleNamespace(conn_kw={}, cert_reqs=None, ca_certs=None, ca_cert_dir=None,
                                    cert_file=None, key_file=None)   # a pool of a proxy: made without the context
    adapter.cert_verify(proxied, "https://example.com/", True, None)
    assert proxied.ca_certs == requests.utils.DEFAULT_CA_BUNDLE_PATH


def test_alpn_is_set_once_on_a_shared_context():
    calls = []

    class Base(ssl.SSLContext):
        def set_alpn_protocols(self, protocols):
            calls.append(list(protocols))
            super().set_alpn_protocols(protocols)

    ctx = net._shared_class(Base)(ssl.PROTOCOL_TLS_CLIENT)
    for _ in range(100):                                    # urllib3 sets it for every connection
        ctx.set_alpn_protocols(u3.ALPN_PROTOCOLS)
    ctx.set_alpn_protocols(["h2"])
    assert calls == [["http/1.1"], ["h2"]]


def test_a_context_made_before_windows_certificates_is_replaced_before_it_connects(monkeypatch):
    adapter = net.make_session().get_adapter("https://learn.ieu.edu.tr/")
    pool = _pool(adapter, "https://learn.ieu.edu.tr/student")
    before = pool.conn_kw["ssl_context"]

    class Truststore(ssl.SSLContext):                       # what truststore.inject_into_ssl puts in urllib3's place
        pass

    monkeypatch.setattr(u3, "SSLContext", Truststore)
    adapter.cert_verify(pool, "https://learn.ieu.edu.tr/student", True, None)
    after = pool.conn_kw["ssl_context"]
    assert after is not before and isinstance(after, Truststore) and after in net._ours
    assert pool._new_conn().ssl_context is after


def test_a_server_that_doesnt_answer_fails_in_seconds_not_a_minute():
    retry = net.make_session().get_adapter("https://panopto.ieu.edu.tr/").max_retries
    assert net.DEFAULT_TIMEOUT[0] <= 6 and retry.connect == 2 and retry.retry_after_max <= 10
    assert retry.parse_retry_after("3600") == retry.retry_after_max     # maintenance: not an hour's wait
    plain = net.plain_session()
    assert plain.get_adapter("https://example.com/").max_retries.total == 0
    assert not isinstance(plain, net._TimeoutSession)                 # as requests.Session(): the caller's timeouts


@pytest.mark.parametrize("var", ["ALL_PROXY", "HTTPS_PROXY"])
def test_a_socks_proxy_is_said_plainly(monkeypatch, var):
    """A SOCKS proxy set on the PC (by a VPN or proxy tool): every connection said "Missing dependencies for SOCKS
    support." in English. Nothing is sent: requests stops before it connects."""
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "all_proxy", "https_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(var, "socks5://127.0.0.1:9")
    with pytest.raises(NetworkError) as err:
        net.make_session().get("https://example.invalid/")
    assert str(err.value) == net.proxy_refused()
    with pytest.raises(requests.exceptions.InvalidSchema) as raw:
        net.plain_session().get("https://example.invalid/", timeout=5)
    assert plain_error(raw.value) == net.proxy_refused()


@pytest.mark.parametrize("var", ["REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_a_certificate_bundle_variable_naming_a_missing_file_is_left_out(monkeypatch, tmp_path, var):
    monkeypatch.setenv(var, str(tmp_path / "gone" / "ca.pem"))      # left by a program since removed
    s = net.make_session()
    assert s.merge_environment_settings("https://learn.ieu.edu.tr/", {}, None, None, None)["verify"] is True
    ca = tmp_path / "company-ca.pem"                                  # one that is there is used as before
    ca.write_text("x")
    monkeypatch.setenv(var, str(ca))
    s = net.plain_session()
    assert s.merge_environment_settings("https://learn.ieu.edu.tr/", {}, None, None, None)["verify"] == str(ca)
