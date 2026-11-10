# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a module's main() in-process, against a scripted fake HTTP server."""
from __future__ import annotations

import json
from urllib.parse import urlparse

import pytest

from ansible.module_utils import basic
from ansible.module_utils.testing import patch_module_args

from ansible_collections.danielhreben.remote_access.plugins.module_utils import http_client

ENV = ("TWINGATE_API_KEY", "TWINGATE_NETWORK", "TAILSCALE_API_KEY", "TAILSCALE_OAUTH_CLIENT_SECRET",
       "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_TAILNET", "TAILSCALE_BASE_URL")


class _Exit(Exception):
    pass


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body


class FakeHttp:
    """Answers by (method, path). An answer is (status, headers, body); body may be
    a callable taking the request and returning the body. A route given several
    answers serves them in turn and then repeats the last."""

    def __init__(self):
        self.routes = {}
        self.requests = []

    def route(self, method, path, *answers):
        self.routes[(method, path)] = list(answers)

    def __call__(self, module, url, data=None, headers=None, method=None, timeout=None, **kw):
        path = urlparse(url).path
        req = dict(method=method or "GET", url=url, path=path, data=data, headers=dict(headers or {}))
        self.requests.append(req)
        answers = self.routes.get((req["method"], path))
        if not answers:
            return None, {"status": 404, "url": url, "body": b"no such route"}
        status, hdrs, body = answers.pop(0) if len(answers) > 1 else answers[0]
        if callable(body):
            body = body(req)
        if not isinstance(body, (str, bytes)):
            body = json.dumps(body)
        body = body.encode() if isinstance(body, str) else body
        info = dict(hdrs, status=status, url=hdrs.get("url", url))
        if 200 <= status < 300:
            return _Resp(body), info
        info["body"] = body
        return None, info

    def find(self, method, path):
        return [r for r in self.requests if r["method"] == method and r["path"] == path]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(http_client, "fetch_url", fake)
    return fake


@pytest.fixture
def run_module(monkeypatch):
    """run_module(main, args, check=False, diff=False) -> the result dict
    (with failed=True when the module failed)."""

    def exit_json(self, **kw):
        raise _Exit(kw)

    def fail_json(self, msg, **kw):
        raise _Exit(dict(kw, msg=msg, failed=True))

    monkeypatch.setattr(basic.AnsibleModule, "exit_json", exit_json)
    monkeypatch.setattr(basic.AnsibleModule, "fail_json", fail_json)

    def run(main, args, check=False, diff=False):
        args = dict(args, _ansible_check_mode=check, _ansible_diff=diff)
        with patch_module_args(args):
            with pytest.raises(_Exit) as e:
                main()
        return e.value.args[0]

    return run
