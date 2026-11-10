# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest

from ansible_collections.danielhreben.remote_access.plugins.modules import tailscale_policy

POLICY = '// example\n{\n\t"grants": [],\n}\n'
ACL = "/api/v2/tailnet/-/acl"


@pytest.fixture
def tailnet(http):
    """A tailnet whose live policy is '{}' with ETag "e1"; uploads succeed."""
    http.route("GET", ACL, (200, {"etag": '"e1"'}, "{}\n"))
    http.route("POST", ACL + "/validate", (200, {}, ""))
    http.route("POST", ACL, (200, {"etag": '"e2"'}, lambda req: req["data"]))
    return http


def run(run_module, check=False, diff=False, **args):
    return run_module(tailscale_policy.main, args, check=check, diff=diff)


def test_content_option_uploads_text(run_module, tailnet):
    r = run(run_module, content=POLICY, api_key="tskey-api-x")
    assert r["changed"] and r["etag"] == '"e2"'
    [post] = tailnet.find("POST", ACL)
    assert post["data"] == POLICY
    assert post["headers"]["If-Match"] == '"e1"'
    assert post["headers"]["Authorization"] == "Bearer tskey-api-x"
    assert post["headers"]["Content-Type"] == "application/hujson"


def test_src_file_is_read(run_module, tailnet, tmp_path):
    f = tmp_path / "policy.hujson"
    f.write_text(POLICY)
    r = run(run_module, src=str(f), api_key="tskey-api-x")
    assert r["changed"]
    assert tailnet.find("POST", ACL)[0]["data"] == POLICY


def test_src_and_content_are_exclusive(run_module, tailnet):
    r = run(run_module, src="/x", content=POLICY, api_key="tskey-api-x")
    assert r["failed"] and "mutually exclusive" in r["msg"]


def test_one_of_src_and_content_is_required(run_module, tailnet):
    r = run(run_module, api_key="tskey-api-x")
    assert r["failed"] and "src" in r["msg"] and "content" in r["msg"]


def test_missing_src_file_fails_cleanly(run_module, tailnet):
    r = run(run_module, src="/nonexistent/policy.hujson", api_key="tskey-api-x")
    assert r["failed"] and "/nonexistent/policy.hujson" in r["msg"]
    assert tailnet.requests == []


def test_api_key_from_TAILSCALE_API_KEY(run_module, tailnet, monkeypatch):
    monkeypatch.setenv("TAILSCALE_API_KEY", "tskey-api-env")
    r = run(run_module, content="{}\n")
    assert not r["changed"]
    assert tailnet.find("GET", ACL)[0]["headers"]["Authorization"] == "Bearer tskey-api-env"


def test_client_secret_from_TAILSCALE_OAUTH_CLIENT_SECRET_is_exchanged(run_module, tailnet, monkeypatch):
    monkeypatch.setenv("TAILSCALE_OAUTH_CLIENT_SECRET", "tskey-client-s")
    monkeypatch.setenv("TAILSCALE_OAUTH_CLIENT_ID", "cid")
    tailnet.route("POST", "/api/v2/oauth/token", (200, {}, {"access_token": "tok"}))
    r = run(run_module, content="{}\n")
    assert not r.get("failed"), r
    [token] = tailnet.find("POST", "/api/v2/oauth/token")
    assert "client_id=cid" in token["data"] and "client_secret=tskey-client-s" in token["data"]
    assert tailnet.find("GET", ACL)[0]["headers"]["Authorization"] == "Bearer tok"


def test_tailnet_and_base_url_from_env(run_module, http, monkeypatch):
    monkeypatch.setenv("TAILSCALE_TAILNET", "example.com")
    monkeypatch.setenv("TAILSCALE_BASE_URL", "https://ts.example.net/")
    path = "/api/v2/tailnet/example.com/acl"
    http.route("GET", path, (200, {"etag": '"e1"'}, "{}\n"))
    http.route("POST", path + "/validate", (200, {}, ""))
    r = run(run_module, content="{}\n", api_key="tskey-api-x")
    assert not r.get("failed"), r
    assert http.requests[0]["url"] == "https://ts.example.net" + path


def test_check_mode_never_posts_the_policy(run_module, tailnet):
    r = run(run_module, content=POLICY, api_key="tskey-api-x", check=True)
    assert r["changed"]
    assert tailnet.find("POST", ACL) == []
    assert len(tailnet.find("POST", ACL + "/validate")) == 1


def test_diff_only_with_diff_mode(run_module, tailnet):
    assert "diff" not in run(run_module, content=POLICY, api_key="tskey-api-x", check=True)
    r = run(run_module, content=POLICY, api_key="tskey-api-x", check=True, diff=True)
    assert r["diff"]["after"] == POLICY and r["diff"]["before"] == "{}\n"


def test_post_omits_if_match_without_etag(run_module, http):
    http.route("GET", ACL, (200, {}, "{}\n"))
    http.route("POST", ACL + "/validate", (200, {}, ""))
    http.route("POST", ACL, (200, {}, POLICY))
    r = run(run_module, content=POLICY, api_key="tskey-api-x")
    assert r["changed"]
    assert "If-Match" not in http.find("POST", ACL)[0]["headers"]


def test_if_match_refuses_a_changed_policy(run_module, tailnet):
    r = run(run_module, content=POLICY, api_key="tskey-api-x", if_match='"e0"')
    assert r["failed"] and "changed since" in r["msg"]
    assert tailnet.find("POST", ACL) == []


def test_http_error_reading_the_policy_fails_cleanly(run_module, http):
    http.route("GET", ACL, (403, {}, '{"message": "forbidden"}'))
    r = run(run_module, content=POLICY, api_key="tskey-api-x")
    assert r["failed"] and "HTTP 403" in r["msg"] and "forbidden" in r["msg"]


def test_412_on_upload_is_reported(run_module, tailnet):
    tailnet.route("POST", ACL, (412, {}, "precondition failed"))
    r = run(run_module, content=POLICY, api_key="tskey-api-x")
    assert r["failed"] and "nothing was uploaded" in r["msg"]


def test_validation_failure_is_reported(run_module, tailnet):
    tailnet.route("POST", ACL + "/validate",
                  (200, {}, {"message": "test(s) failed", "data": [{"user": "a@example.com", "errors": ["denied"]}]}))
    r = run(run_module, content=POLICY, api_key="tskey-api-x")
    assert r["failed"] and "a@example.com: denied" in r["msg"]
    assert tailnet.find("POST", ACL) == []
