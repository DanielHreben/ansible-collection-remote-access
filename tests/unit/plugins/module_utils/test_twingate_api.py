# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Twingate GraphQL client, against a fake Admin API."""
from __future__ import annotations

import json
import re
from unittest import mock

import pytest

from ansible_collections.danielhreben.remote_access.plugins.module_utils import http_client
from ansible_collections.danielhreben.remote_access.plugins.module_utils import twingate as tg

URL = "https://example.twingate.com/api/graphql/"


class _Resp:
    def __init__(self, body, url=None):
        self.body, self.url = body, url

    def read(self):
        return self.body

    def geturl(self):
        return self.url


def _page(items, first, after):
    start = int(after or 0)
    chunk = items[start:start + first]
    end = start + len(chunk)
    return {"pageInfo": {"endCursor": str(end), "hasNextPage": end < len(items)},
            "edges": [{"node": x} for x in chunk]}


class FakeTwingate:
    """Serves remoteNetworks, groups, resources (with paged access) and
    resource mutations, paging by the query's first/after variables."""

    def __init__(self, networks=(), groups=(), resources=()):
        self.networks = [{"id": "N-" + n, "name": n} for n in networks]
        self.groups = [{"id": "G-" + g, "name": g} for g in groups]
        self.resources = list(resources)
        self.requests = []
        self.mutation_answer = {"ok": True, "error": None}
        self.raw = None  # (status, body) to answer every request with

    def __call__(self, module, url, data=None, headers=None, method=None, timeout=None, **kw):
        req = json.loads(data) if data else {}
        self.requests.append(dict(url=url, method=method, headers=headers or {}, **req))
        if self.raw:
            status, body = self.raw
            if status == 200:
                return _Resp(body.encode(), url), {"status": status, "url": url}
            # Like fetch_url: the HTTPError, already read, with the body in info.
            return _Resp(b"", url), {"status": status, "url": url, "body": body.encode()}
        q, v = req["query"], req["variables"]
        first, after = v.get("first"), v.get("after")
        if q.lstrip().startswith("mutation"):
            data = {"r": self.mutation_answer}
        elif "remoteNetworks(" in q:
            data = {"remoteNetworks": _page(self.networks, first, after)}
        elif "groups(" in q:
            data = {"groups": _page(self.groups, first, after)}
        elif "resource(id:" in q:
            r = next(r for r in self.resources if r.get("id") == v["id"])
            data = {"resource": {"access": _page(r["_access"], first, after)}}
        else:
            nodes = []
            for r in self.resources:
                if r["__typename"] != "NetworkResource":
                    nodes.append({"__typename": r["__typename"], "id": r["id"], "name": r["name"]})
                    continue
                n = {k: x for k, x in r.items() if k != "_access"}
                n["access"] = _page(r["_access"], first, None)
                nodes.append(n)
            data = {"resources": _page(nodes, first, after)}
        return _Resp(json.dumps({"data": data}).encode(), url), {"status": 200, "url": url}


def resource(name, groups=("Engineering",), extra_access=()):
    access = [{"__typename": "Group", "id": "G-" + g, "name": g} for g in groups] + list(extra_access)
    return {"__typename": "NetworkResource", "id": "R-" + name, "name": name, "alias": None,
            "isVisible": True, "isBrowserShortcutEnabled": False, "address": {"value": name},
            "remoteNetwork": {"id": "N-Office", "name": "Office"},
            "protocols": {"allowIcmp": False, "tcp": {"policy": "RESTRICTED", "ports": [{"start": 443, "end": 443}]},
                          "udp": {"policy": "RESTRICTED", "ports": []}},
            "_access": access}


@pytest.fixture
def module():
    m = mock.Mock()
    m.params = {"timeout": 30}
    return m


@pytest.fixture
def fake(monkeypatch):
    f = FakeTwingate(networks=["Office"], groups=["Engineering", "Contractors"])
    monkeypatch.setattr(http_client, "fetch_url", f)
    return f


def api(module):
    return tg.TwingateApi(module, URL, "k")


def test_pages_through_resources_groups_and_networks(module, fake):
    fake.resources = [resource(f"r{i}.example.com") for i in range(120)]
    fake.groups = [{"id": f"G-{i}", "name": f"g{i}"} for i in range(60)]
    st = api(module).read_state()
    assert [r["name"] for r in st["resources"]] == [f"r{i}.example.com" for i in range(120)]
    assert len(st["groups"]) == 60 and st["groups"]["g59"] == ["G-59"]
    assert st["networks"] == {"Office": ["N-Office"]}
    pages = [r for r in fake.requests if "resources(" in r["query"]]
    assert [p["variables"]["after"] for p in pages] == [None, "50", "100"]
    assert all(p["variables"]["first"] == 50 for p in fake.requests)


def test_duplicate_names_keep_every_id(module, fake):
    fake.groups = [{"id": "G-1", "name": "Ops"}, {"id": "G-2", "name": "Ops"}]
    assert api(module).read_state()["groups"] == {"Ops": ["G-1", "G-2"]}


def test_fetches_remaining_access_entries_for_a_resource(module, fake):
    many = [f"g{i}" for i in range(75)]
    fake.resources = [resource("wiki.example.com", groups=many)]
    st = api(module).read_state()
    edges = st["resources"][0]["access"]["edges"]
    assert sorted(e["node"]["name"] for e in edges) == sorted(many)
    [more] = [r for r in fake.requests if "resource(id:" in r["query"]]
    assert more["variables"] == {"id": "R-wiki.example.com", "first": 50, "after": "50"}


def test_skips_non_network_resources_but_keeps_their_names(module, fake):
    fake.resources = [resource("wiki.example.com"),
                      {"__typename": "KubernetesResource", "id": "K-1", "name": "cluster", "_access": []}]
    st = api(module).read_state()
    assert [r["name"] for r in st["resources"]] == ["wiki.example.com"]
    assert st["others"] == {"cluster": "KubernetesResource"}


def test_resources_query_selects_network_resources(module, fake):
    api(module).read_state()
    [q] = [r["query"] for r in fake.requests if "resources(" in r["query"]]
    assert "... on NetworkResource" in q


def test_service_account_access_is_ignored(module, fake):
    sa = {"__typename": "ServiceAccount", "id": "S-1", "name": "ci"}
    fake.resources = [resource("wiki.example.com", extra_access=[sa])]
    full = tg.live_full(api(module).read_state()["resources"][0])
    assert full["groups"] == ["Engineering"]


def test_sends_the_api_key(module, fake):
    api(module).read_state()
    h = fake.requests[0]["headers"]
    assert h["X-API-KEY"] == "k" and h["Content-Type"] == "application/json"
    assert fake.requests[0]["url"] == URL and fake.requests[0]["method"] == "POST"


def test_graphql_errors_become_TwingateError(module, fake):
    fake.raw = (200, json.dumps({"errors": [{"message": "bad field"}, {"message": "worse"}]}))
    with pytest.raises(tg.TwingateError, match="bad field; worse"):
        api(module).read_state()


def test_graphql_errors_in_a_400_are_reported(module, fake):
    fake.raw = (400, json.dumps({"errors": [{"message": "Variable '$protocols' has an invalid value"}]}))
    with pytest.raises(tg.TwingateError, match="invalid value"):
        api(module).read_state()


def test_http_errors_become_TwingateError(module, fake):
    fake.raw = (401, '{"error": "invalid token"}')
    with pytest.raises(tg.TwingateError, match="HTTP 401.*invalid token"):
        api(module).read_state()


def test_non_json_answer_names_api_url(module, fake):
    fake.raw = (200, "<html><body>Sign in</body></html>")
    with pytest.raises(tg.TwingateError, match="not JSON.*api_url"):
        api(module).read_state()


def test_mutation_not_ok_raises_with_name(module, fake):
    fake.mutation_answer = {"ok": False, "error": "address is invalid"}
    with pytest.raises(tg.TwingateError, match="resourceCreate wiki.example.com: address is invalid"):
        api(module).apply("create", {"name": "wiki.example.com", "address": "x"})


def test_variable_declarations_are_strict(module, fake):
    a = api(module)
    a.apply("create", {"name": "n", "address": "a", "remoteNetworkId": "N", "groupIds": ["G"],
                       "protocols": {}, "isVisible": True, "isBrowserShortcutEnabled": False, "alias": "x"})
    a.apply("update", {"id": "R", "alias": None, "addedGroupIds": ["G"], "removedGroupIds": ["H"]})
    a.apply("delete", {"id": "R"})
    create, update, delete = (r["query"] for r in fake.requests)
    decl = dict(re.findall(r"\$(\w+): ([\w\[\]!]+)", create.split(")")[0]))
    assert decl == {"name": "String!", "address": "String!", "remoteNetworkId": "ID!", "groupIds": "[ID!]!",
                    "protocols": "ProtocolsInput!", "isVisible": "Boolean!",
                    "isBrowserShortcutEnabled": "Boolean!", "alias": "String!"}
    assert "resourceCreate(" in create and "name: $name" in create
    assert dict(re.findall(r"\$(\w+): ([\w\[\]!]+)", update.split(")")[0])) == {
        "id": "ID!", "alias": "String", "addedGroupIds": "[ID!]!", "removedGroupIds": "[ID!]!"}
    assert "resourceUpdate(" in update and "resourceDelete(id: $id)" in delete
    assert fake.requests[1]["variables"]["alias"] is None


def test_mutations_are_not_retried_on_5xx_and_their_outcome_is_unknown(module, fake, monkeypatch):
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    fake.raw = (503, "busy")
    with pytest.raises(tg.TwingateError, match="HTTP 503") as e:
        api(module).apply("delete", {"id": "R"})
    assert len(fake.requests) == 1
    assert e.value.unknown is True


def test_a_refused_mutation_is_known_not_applied(module, fake):
    fake.mutation_answer = {"ok": False, "error": "nope"}
    with pytest.raises(tg.TwingateError) as e:
        api(module).apply("delete", {"id": "R"})
    assert e.value.unknown is False


def test_a_failed_read_is_not_marked_unknown(module, fake):
    fake.raw = (401, "denied")
    with pytest.raises(tg.TwingateError) as e:
        api(module).read_state()
    assert e.value.unknown is False


class TestResolve:
    def run(self, module, status, final, network="acme"):
        """fetch_url reports the final URL through the response object; on an
        HTTP error its info["url"] is still the request URL."""
        calls = []

        def fetch(module, url, **kw):
            calls.append(dict(url=url, **kw))
            if status == -1:
                return None, {"status": -1, "url": url, "msg": "no route"}
            if status == 200:
                return _Resp(b"", final), {"status": 200, "url": final}
            return _Resp(b"", final), {"status": status, "url": url, "body": b""}

        with mock.patch.object(http_client, "fetch_url", fetch):
            return tg.resolve_api_url(module, network, "k"), calls

    def test_uses_the_regional_host(self, module):
        url, calls = self.run(module, 200, "https://acme.eu1.twingate.com/login")
        assert url == "https://acme.eu1.twingate.com/api/graphql/"
        assert calls[0]["url"] == "https://acme.twingate.com"
        assert calls[0]["headers"]["X-API-KEY"] == "k"

    def test_key_is_not_sent_on_to_redirect_targets(self, module):
        url, calls = self.run(module, 200, "https://acme.twingate.com/")
        assert calls[0]["unredirected_headers"] == ["X-API-KEY"]

    def test_uses_the_regional_host_even_when_it_answers_with_an_error(self, module):
        url, calls = self.run(module, 401, "https://acme.eu1.twingate.com/")
        assert url == "https://acme.eu1.twingate.com/api/graphql/"

    def test_network_name_case_does_not_matter(self, module):
        url, calls = self.run(module, 200, "https://acme.eu1.twingate.com/", network="Acme")
        assert url == "https://acme.eu1.twingate.com/api/graphql/"
        assert calls[0]["url"] == "https://acme.twingate.com"

    def test_lookup_is_not_retried(self, module):
        url, calls = self.run(module, -1, None)
        assert len(calls) == 1

    def test_no_redirect_keeps_the_network_host(self, module):
        url, calls = self.run(module, 200, "https://acme.twingate.com/")
        assert url == "https://acme.twingate.com/api/graphql/"

    @pytest.mark.parametrize("final", ["https://auth.twingate.com/login", "https://acme.evil.example/",
                                       "http://acme.eu1.twingate.com/", "https://acmex.twingate.com/"])
    def test_ignores_a_host_that_is_not_this_networks(self, module, final):
        url, calls = self.run(module, 200, final)
        assert url == "https://acme.twingate.com/api/graphql/"

    def test_falls_back_on_error(self, module):
        url, calls = self.run(module, -1, None)
        assert url == "https://acme.twingate.com/api/graphql/"
