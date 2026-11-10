# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from unittest import mock

import pytest

from ansible_collections.danielhreben.remote_access.plugins.module_utils import http_client


class FakeResp:
    def __init__(self, body, url):
        self.body, self.url = body, url

    def read(self):
        return self.body

    def geturl(self):
        return self.url


def scripted(*answers):
    """A fake fetch_url answering (status, headers, body) in turn. Like the real
    one, an HTTP error comes back as an already-read response object (read()
    gives b"") with the body in info["body"]; a connection error as None."""
    calls = []

    def fetch_url(module, url, **kw):
        calls.append(dict(url=url, **kw))
        status, headers, body = answers[min(len(calls), len(answers)) - 1]
        final = headers.pop("final_url", url) if "final_url" in headers else url
        info = dict(headers, status=status, url=url)
        if status == -1:
            return None, info
        if 200 <= status < 300:
            info["url"] = final
            return FakeResp(body.encode(), final), info
        info["body"] = body.encode()
        return FakeResp(b"", final), info

    return fetch_url, calls


@pytest.fixture
def module():
    m = mock.Mock()
    m.params = {"timeout": 30}
    return m


def run(module, answers, **kw):
    fetch, calls = scripted(*answers)
    slept = []
    with mock.patch.object(http_client, "fetch_url", fetch):
        r = http_client.call(module, "https://api.example.com/x", sleep=slept.append, **kw)
    return r, calls, slept


def test_success_returns_body_and_lowercase_headers(module):
    r, calls, slept = run(module, [(200, {"etag": '"e1"'}, "hello")])
    assert (r.status, r.body, r.headers["etag"]) == (200, "hello", '"e1"')
    assert calls[0]["timeout"] == 30
    assert slept == []


def test_error_body_is_returned_as_text(module):
    r, calls, slept = run(module, [(404, {}, "nope")], retry_5xx=False)
    assert (r.status, r.body) == (404, "nope")


def test_429_waits_retry_after_then_succeeds(module):
    r, calls, slept = run(module, [(429, {"retry-after": "7"}, ""), (200, {}, "ok")])
    assert r.status == 200
    assert slept == [7]


def test_retry_after_is_capped(module):
    r, calls, slept = run(module, [(429, {"retry-after": "999"}, ""), (200, {}, "ok")])
    assert slept == [120]


def test_unparseable_retry_after_falls_back_to_backoff(module):
    r, calls, slept = run(module, [(429, {"retry-after": "soon"}, ""), (200, {}, "ok")])
    assert slept == [2]


def test_503_retried_for_reads_with_backoff(module):
    r, calls, slept = run(module, [(503, {}, ""), (503, {}, ""), (200, {}, "ok")])
    assert r.status == 200
    assert slept == [2, 5]


def test_503_not_retried_when_retry_5xx_false(module):
    r, calls, slept = run(module, [(503, {}, "busy"), (200, {}, "ok")], retry_5xx=False)
    assert r.status == 503
    assert len(calls) == 1


def test_429_still_retried_when_retry_5xx_false(module):
    r, calls, slept = run(module, [(429, {}, ""), (200, {}, "ok")], retry_5xx=False)
    assert r.status == 200
    assert slept == [2]


def test_connection_error_retried_once_and_only_for_reads(module):
    r, calls, slept = run(module, [(-1, {"msg": "refused"}, ""), (200, {}, "ok")])
    assert (len(calls), r.status, slept) == (2, 200, [2])
    r, calls, slept = run(module, [(-1, {"msg": "refused"}, "")])
    assert (len(calls), r.status) == (2, -1)
    r, calls, slept = run(module, [(-1, {"msg": "refused"}, ""), (200, {}, "ok")], retry_5xx=False)
    assert len(calls) == 1
    assert r.status == -1
    assert r.headers["msg"] == "refused"


def test_gives_up_after_five_tries(module):
    r, calls, slept = run(module, [(429, {}, "slow down")])
    assert len(calls) == 5
    assert r.status == 429
    assert slept == [2, 5, 15, 30]


def test_passes_method_data_and_headers(module):
    r, calls, slept = run(module, [(200, {}, "")], method="POST", data="{}", headers={"A": "b"})
    assert (calls[0]["method"], calls[0]["data"], calls[0]["headers"]) == ("POST", "{}", {"A": "b"})


def test_error_body_comes_from_info(module):
    # fetch_url hands back the (already read) HTTPError as the response.
    r, calls, slept = run(module, [(400, {}, '{"message": "invalid policy"}')], retry_5xx=False)
    assert (r.status, r.body) == (400, '{"message": "invalid policy"}')


def test_final_url_after_redirects_even_on_an_error(module):
    r, calls, slept = run(module, [(401, {"final_url": "https://acme.eu1.example.com/"}, "")], retry_5xx=False)
    assert r.url == "https://acme.eu1.example.com/"
    r, calls, slept = run(module, [(200, {"final_url": "https://acme.eu1.example.com/x"}, "")])
    assert r.url == "https://acme.eu1.example.com/x"


def test_url_of_a_connection_error_is_the_request_url(module):
    r, calls, slept = run(module, [(-1, {}, "")], retry_5xx=False)
    assert r.url == "https://api.example.com/x"


def test_unredirected_headers_are_passed_on(module):
    r, calls, slept = run(module, [(200, {}, "")], unredirected_headers=["X-API-KEY"])
    assert calls[0]["unredirected_headers"] == ["X-API-KEY"]
