# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""The tailscale_policy logic, with the API faked."""
from __future__ import annotations

import json

import pytest

from ansible_collections.danielhreben.remote_access.plugins.module_utils import tailscale as tp

POLICY = '// home\n{\n\t"grants": [],\n}\n'


class FakeApi:
    """Records calls; answers like api.tailscale.com."""

    def __init__(self, live=POLICY, etag='"e1"', validate=None, post_status=200):
        self.live, self.etag = live, etag
        self.validate_answer = validate if validate is not None else {}
        self.post_status = post_status
        self.calls = []

    def get_policy(self):
        self.calls.append("get")
        return self.live, self.etag

    def validate(self, text):
        self.calls.append("validate")
        return self.validate_answer

    def post_policy(self, text, if_match):
        self.calls.append(("post", if_match))
        if self.post_status == 412:
            raise tp.PolicyError("the policy changed in the console")
        self.live, self.etag = text, '"e2"'
        return self.etag


class TestReconcile:
    def test_same_text_changes_nothing(self):
        api = FakeApi()
        r = tp.reconcile(api, POLICY, check_mode=False)
        assert not r["changed"]
        assert api.calls == ["get", "validate"]
        assert r["etag"] == '"e1"'

    def test_trailing_newlines_are_not_a_change(self):
        r = tp.reconcile(FakeApi(live=POLICY + "\n\n"), POLICY, check_mode=False)
        assert not r["changed"]

    def test_tailscales_reformatting_is_not_a_change(self):
        # Tailscale reformats the file on save: wraps lists, realigns columns.
        mine = '// home\n{\n\t"a":  ["x", "y"], // two\n\t"b": 1,\n}\n'
        theirs = '// home\n{\n\t"a": [\n\t\t"x",\n\t\t"y",\n\t], // two\n\t"b": 1,\n}\n'
        api = FakeApi(live=theirs)
        r = tp.reconcile(api, mine, check_mode=False)
        assert not r["changed"]
        assert "diff" not in r
        assert ("post", '"e1"') not in api.calls

    def test_a_comment_change_is_a_change(self):
        r = tp.reconcile(FakeApi(), POLICY.replace("// home", "// house"), check_mode=True)
        assert r["changed"]

    def test_a_value_change_is_a_change(self):
        r = tp.reconcile(FakeApi(), POLICY.replace("[]", '["x"]'), check_mode=True)
        assert r["changed"]

    def test_check_mode_reports_the_change_and_posts_nothing(self):
        api = FakeApi(live=POLICY.replace("[]", '["x"]'))
        r = tp.reconcile(api, POLICY, check_mode=True)
        assert r["changed"]
        assert api.calls == ["get", "validate"]
        assert r["diff"]["after"] == POLICY
        assert '["x"]' in r["diff"]["before"]

    def test_apply_posts_with_the_etag_it_read(self):
        api = FakeApi(live="{}\n")
        r = tp.reconcile(api, POLICY, check_mode=False)
        assert r["changed"]
        assert api.calls == ["get", "validate", ("post", '"e1"')]
        assert r["etag"] == '"e2"'

    def test_if_match_refuses_a_policy_edited_since_the_check(self):
        api = FakeApi(live="{}\n", etag='"e9"')
        with pytest.raises(tp.PolicyError, match="changed since"):
            tp.reconcile(api, POLICY, check_mode=False, if_match='"e1"')
        assert ("post", '"e9"') not in api.calls

    def test_failing_policy_tests_stop_before_posting(self):
        answer = {"message": "test(s) failed", "data": [{"user": "a@example.com", "errors": ["x denied"]}]}
        api = FakeApi(live="{}\n", validate=answer)
        with pytest.raises(tp.PolicyError, match="x denied"):
            tp.reconcile(api, POLICY, check_mode=False)
        assert api.calls == ["get", "validate"]

    def test_a_race_after_the_read_is_an_error(self):
        api = FakeApi(live="{}\n", post_status=412)
        with pytest.raises(tp.PolicyError, match="changed in the console"):
            tp.reconcile(api, POLICY, check_mode=False)


class TestHujson:
    def test_comments_and_trailing_commas(self):
        text = '/* a */ {\n "u": "http://x//y", // b\n "l": [1, 2,],\n}'
        assert tp.hujson_meaning(text) == ({"u": "http://x//y", "l": [1, 2]}, ["/* a */", "// b"])

    def test_escaped_quote_in_string(self):
        assert tp.hujson_meaning('{"q": "a\\"//b"}')[0] == {"q": 'a"//b'}

    def test_unterminated_string_is_a_difference_not_a_crash(self):
        assert tp.same_policy('{"a": "x', "{}") is False

    def test_unterminated_block_comment_is_a_difference_not_a_crash(self):
        assert tp.same_policy("{} /* open", "{}") is False


class TestValidateAnswer:
    def test_empty_answer_passes(self):
        tp.raise_for_validation({})

    def test_message_without_data_fails(self):
        with pytest.raises(tp.PolicyError, match="bad json"):
            tp.raise_for_validation({"message": "bad json"})


class TestToken:
    def test_api_key_is_used_as_is(self):
        assert tp.bearer_token("tskey-api-abc", None, post_form=None) == "tskey-api-abc"

    def test_oauth_secret_is_exchanged(self):
        seen = {}

        def post_form(url, form):
            seen.update(url=url, form=form)
            return json.dumps({"access_token": "tok"})

        assert tp.bearer_token("tskey-client-s", "cid", post_form=post_form) == "tok"
        assert seen["url"] == "/api/v2/oauth/token"
        assert seen["form"]["client_id"] == "cid"
        assert seen["form"]["client_secret"] == "tskey-client-s"
        assert seen["form"]["grant_type"] == "client_credentials"

    def test_oauth_secret_needs_a_client_id(self):
        with pytest.raises(tp.PolicyError, match="oauth_client_id"):
            tp.bearer_token("tskey-client-s", None, post_form=None)

    def test_token_answer_without_access_token_fails_cleanly(self):
        with pytest.raises(tp.PolicyError, match="access_token"):
            tp.bearer_token("tskey-client-s", "cid", post_form=lambda url, form: "<html>")
