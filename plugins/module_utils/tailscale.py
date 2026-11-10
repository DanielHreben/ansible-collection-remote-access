# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tailscale's policy file: compare, validate and upload through the admin API."""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode

from ansible.module_utils.basic import env_fallback

from ansible_collections.danielhreben.remote_access.plugins.module_utils.http_client import call

# The connection options every Tailscale module takes (doc fragment: tailscale).
ARGUMENT_SPEC = dict(
    api_key=dict(type="str", required=True, no_log=True,
                 fallback=(env_fallback, ["TAILSCALE_API_KEY", "TAILSCALE_OAUTH_CLIENT_SECRET"])),
    oauth_client_id=dict(type="str", fallback=(env_fallback, ["TAILSCALE_OAUTH_CLIENT_ID"])),
    tailnet=dict(type="str", default="-", fallback=(env_fallback, ["TAILSCALE_TAILNET"])),
    api_url=dict(type="str", default="https://api.tailscale.com", fallback=(env_fallback, ["TAILSCALE_BASE_URL"])),
    validate_certs=dict(type="bool", default=True),
    timeout=dict(type="int", default=30),
)


class PolicyError(Exception):
    pass


def bearer_token(api_key, oauth_client_id, post_form):
    """The token for the Authorization header: an API access token as it is, or
    an OAuth client secret exchanged for a short-lived token."""
    if not api_key.startswith("tskey-client-"):
        return api_key
    if not oauth_client_id:
        raise PolicyError("api_key is an OAuth client secret: oauth_client_id is required")
    body = post_form("/api/v2/oauth/token", {
        "client_id": oauth_client_id,
        "client_secret": api_key,
        "grant_type": "client_credentials",
    })
    try:
        return json.loads(body)["access_token"]
    except (ValueError, KeyError, TypeError):
        raise PolicyError(f"OAuth token exchange: no access_token in the answer {body[:200]!r}")


def raise_for_validation(answer):
    """The validate endpoint answers {} when the policy and its tests pass."""
    if not answer or not answer.get("message"):
        return
    lines = [answer["message"]]
    for d in answer.get("data") or []:
        who = d.get("user") or d.get("src") or ""
        lines += [f"{who}: {e}" for e in d.get("errors") or []]
    raise PolicyError("; ".join(lines))


def hujson_meaning(text):
    """(value, comments) of a HuJSON text: what survives Tailscale's
    reformatting on save, which rewraps lists and realigns columns."""
    out, comments, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments.append(text[i:j].rstrip())
            i = j
        elif text.startswith("/*", i):
            j = text.index("*/", i) + 2
            comments.append(text[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out))), comments


def same_policy(a, b):
    """True when two policy texts differ at most in formatting."""
    if a.rstrip("\n") == b.rstrip("\n"):
        return True
    try:
        return hujson_meaning(a) == hujson_meaning(b)
    except (ValueError, IndexError):
        return False


def reconcile(api, desired, check_mode, if_match=None):
    live, etag = api.get_policy()
    if if_match and etag != if_match:
        raise PolicyError(f"the live policy changed since the check (ETag {etag}, expected {if_match})")
    raise_for_validation(api.validate(desired))
    result = {"changed": not same_policy(live, desired), "etag": etag}
    if result["changed"]:
        result["diff"] = {"before": live, "after": desired,
                          "before_header": "tailnet (live)", "after_header": "desired"}
        if not check_mode:
            result["etag"] = api.post_policy(desired, etag)
    return result


class TailscaleApi:
    def __init__(self, module, base_url, tailnet):
        self.module, self.base = module, base_url.rstrip("/")
        self.acl = f"/api/v2/tailnet/{quote(tailnet, safe='')}/acl"
        self.token = None

    def _call(self, path, method="GET", data=None, headers=None, retry_5xx=True):
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        h.update(headers or {})
        return call(self.module, self.base + path, method=method, data=data, headers=h, retry_5xx=retry_5xx)

    @staticmethod
    def _fail(what, r):
        detail = r.body[:300] if r.body else r.headers.get("msg", "")
        return PolicyError(f"{what} failed: HTTP {r.status} {detail}".rstrip())

    def post_form(self, path, form):
        r = self._call(path, "POST", urlencode(form), {"Content-Type": "application/x-www-form-urlencoded"})
        if r.status != 200:
            raise self._fail("OAuth token exchange", r)
        return r.body

    def get_policy(self):
        r = self._call(self.acl, headers={"Accept": "application/hujson"})
        if r.status != 200:
            raise self._fail("reading the policy", r)
        return r.body, r.headers.get("etag")

    def validate(self, text):
        r = self._call(self.acl + "/validate", "POST", text, {"Content-Type": "application/hujson"})
        if r.status not in (200, 400):
            raise self._fail("validating the policy", r)
        if not r.body.strip():
            return {}
        try:
            return json.loads(r.body)
        except ValueError:
            raise PolicyError(f"validating the policy: an answer that is not JSON: {r.body[:300]!r}")

    def post_policy(self, text, etag):
        headers = {"Content-Type": "application/hujson", "Accept": "application/hujson"}
        if etag:
            headers["If-Match"] = etag
        r = self._call(self.acl, "POST", text, headers, retry_5xx=False)
        if r.status == 412:
            raise PolicyError("the policy changed in the console while this ran; nothing was uploaded")
        if r.status != 200:
            raise self._fail("uploading the policy", r)
        return r.headers.get("etag")


def connect(module):
    """A TailscaleApi for the module's connection options, with its token."""
    p = module.params
    api = TailscaleApi(module, p["api_url"], p["tailnet"])
    api.token = bearer_token(p["api_key"], p["oauth_client_id"], api.post_form)
    if api.token != p["api_key"]:  # an exchanged OAuth token: keep it out of any output
        module.no_log_values.add(api.token)
    return api
