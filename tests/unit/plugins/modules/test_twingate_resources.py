# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

import pytest

from ansible_collections.danielhreben.remote_access.plugins.module_utils import twingate as tg
from ansible_collections.danielhreben.remote_access.plugins.modules import twingate_resource_info
from ansible_collections.danielhreben.remote_access.plugins.modules import twingate_resources


def node(name, tcp=443, groups=("Engineering",)):
    return {"__typename": "NetworkResource", "id": "R-" + name, "name": name, "alias": None,
            "isVisible": True, "isBrowserShortcutEnabled": False, "address": {"value": name},
            "remoteNetwork": {"id": "N-Office", "name": "Office"},
            "protocols": {"allowIcmp": False,
                          "tcp": {"policy": "RESTRICTED", "ports": [{"start": tcp, "end": tcp}]},
                          "udp": {"policy": "RESTRICTED", "ports": []}},
            "access": {"edges": [{"node": {"__typename": "Group", "id": "G-" + g, "name": g}} for g in groups]}}


STATE = {"resources": [node("wiki.example.com"), node("old.example.com")],
         "groups": {"Engineering": ["G-Engineering"], "Contractors": ["G-Contractors"]},
         "networks": {"Office": ["N-Office"]}}
WIKI = {"name": "wiki.example.com", "tcp": [443], "groups": ["Engineering"]}
OLD = {"name": "old.example.com", "tcp": [443], "groups": ["Engineering"]}
NEW = {"name": "new.example.com", "tcp": [22], "groups": ["Contractors"]}


@pytest.fixture
def api(monkeypatch):
    """Replaces the client: records the URL it was given and the changes applied."""

    class FakeApi:
        last = None
        resolved = []
        fail_on = None
        unknown = False

        def __init__(self, module, url, key):
            self.url, self.key, self.calls = url, key, []
            FakeApi.last = self

        def read_state(self):
            return copy.deepcopy(STATE)

        def apply(self, op, variables):
            self.calls.append((op, variables))
            if len(self.calls) == FakeApi.fail_on:
                raise tg.TwingateError("resourceUpdate wiki.example.com: boom", unknown=FakeApi.unknown)

    def resolve(module, network, key):
        FakeApi.resolved.append((network, key))
        return f"https://{network}.twingate.com/api/graphql/"

    monkeypatch.setattr(tg, "TwingateApi", FakeApi)
    monkeypatch.setattr(tg, "resolve_api_url", resolve)
    return FakeApi


def run(run_module, check=False, diff=False, **args):
    args = dict({"network": "example", "api_key": "k", "remote_network": "Office"}, **args)
    return run_module(twingate_resources.main, args, check=check, diff=diff)


def test_nothing_to_do(run_module, api):
    r = run(run_module, resources=[WIKI, OLD])
    assert not r["changed"] and api.last.calls == []
    assert r["resources"] == [WIKI, OLD]
    assert "diff" not in r


def test_check_mode_reports_but_does_not_apply(run_module, api):
    r = run(run_module, resources=[dict(WIKI, tcp=[8443]), OLD], check=True)
    assert r["changed"] and r["updated"] == ["wiki.example.com"]
    assert api.last.calls == []


def test_unmanaged_is_reported(run_module, api):
    r = run(run_module, resources=[WIKI])
    assert not r["changed"] and r["unmanaged"] == ["old.example.com"]


def test_apply_runs_ops_in_order(run_module, api):
    r = run(run_module, resources=[NEW, dict(WIKI, tcp=[8443])], prune=True)
    assert [op for op, v in api.last.calls] == ["create", "update", "delete"]
    assert (r["created"], r["updated"], r["deleted"]) == (["new.example.com"], ["wiki.example.com"], ["old.example.com"])


def test_partial_apply_failure_lists_what_was_done(run_module, api):
    api.fail_on = 2
    r = run(run_module, resources=[NEW, dict(WIKI, tcp=[8443])], prune=True)
    assert r["failed"]
    assert "boom" in r["msg"]
    assert "Done before the failure: created new.example.com." in r["msg"]
    assert "Failed: updated wiki.example.com." in r["msg"]
    assert "Not done: deleted old.example.com." in r["msg"]


def test_partial_apply_with_an_unknown_outcome_says_so(run_module, api):
    api.fail_on, api.unknown = 2, True
    r = run(run_module, resources=[NEW, dict(WIKI, tcp=[8443])], prune=True)
    assert r["failed"]
    assert "Outcome unknown (check the console): updated wiki.example.com." in r["msg"]
    assert "Not done: deleted old.example.com." in r["msg"]


def test_fingerprint_round_trip(run_module, api):
    plan = run(run_module, resources=[NEW, WIKI, OLD], check=True)
    r = run(run_module, resources=[NEW, WIKI, OLD], if_match=plan["fingerprint"])
    assert r["changed"] and [op for op, v in api.last.calls] == ["create"]


def test_if_match_mismatch_fails(run_module, api):
    r = run(run_module, resources=[NEW, WIKI, OLD], if_match="0" * 64)
    assert r["failed"] and "changed since the check" in r["msg"]
    assert api.last.calls == []


def test_prune_with_empty_list_refuses(run_module, api):
    r = run(run_module, resources=[], prune=True)
    assert r["failed"] and "every resource" in r["msg"]


def test_diff_mode_shows_the_short_forms(run_module, api):
    r = run(run_module, resources=[dict(WIKI, tcp=[8443]), OLD], check=True, diff=True)
    assert "wiki.example.com" in r["diff"]["before"] and "8443" in r["diff"]["after"]
    assert "old.example.com" not in r["diff"]["after"]


def test_unknown_resource_key_rejected_by_ansible(run_module, api):
    r = run(run_module, resources=[{"name": "x", "port": 22}])
    assert r["failed"] and "port" in r["msg"]


def test_ansible_fills_suboption_defaults(run_module, api):
    r = run(run_module, resources=[{"name": "wiki.example.com", "tcp": 443, "groups": ["Engineering"]}, OLD])
    assert not r["changed"]


def test_env_fallbacks(run_module, api, monkeypatch):
    monkeypatch.setenv("TWINGATE_NETWORK", "acme")
    monkeypatch.setenv("TWINGATE_API_KEY", "envkey")
    r = run_module(twingate_resources.main, {"remote_network": "Office", "resources": [WIKI, OLD]})
    assert not r.get("failed"), r
    assert api.resolved == [("acme", "envkey")]
    assert api.last.url == "https://acme.twingate.com/api/graphql/" and api.last.key == "envkey"


def test_api_url_skips_resolution(run_module, api):
    r = run(run_module, network=None, api_url="https://x.example.com/api/graphql/", resources=[WIKI, OLD])
    assert not r.get("failed"), r
    assert api.resolved == [] and api.last.url == "https://x.example.com/api/graphql/"


def test_network_or_api_url_required(run_module, api):
    r = run_module(twingate_resources.main, {"api_key": "k", "resources": [WIKI]})
    assert r["failed"] and "network" in r["msg"] and "api_url" in r["msg"]


def test_null_network_without_api_url_fails_cleanly(run_module, api):
    r = run(run_module, network=None, resources=[WIKI])
    assert r["failed"] and "network" in r["msg"] and "api_url" in r["msg"]


@pytest.mark.parametrize("bad", ["evil.example/x?", "a b", "-acme", "acme.example.com"])
def test_bad_network_name_fails_before_any_request(run_module, api, bad):
    r = run(run_module, network=bad, resources=[WIKI])
    assert r["failed"] and "not a network name" in r["msg"]
    assert api.resolved == []


def test_info_returns_short_forms_and_names(run_module, api):
    r = run_module(twingate_resource_info.main, {"network": "example", "api_key": "k", "remote_network": "Office"})
    assert r["resources"] == [WIKI, OLD]
    assert r["groups"] == ["Contractors", "Engineering"]
    assert r["remote_networks"] == ["Office"]
    assert r["changed"] is False


def test_info_without_default_network_names_it(run_module, api):
    r = run_module(twingate_resource_info.main, {"network": "example", "api_key": "k"}, check=True)
    assert r["resources"][0]["remote_network"] == "Office"
