# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalising and planning Twingate resources (no API)."""
from __future__ import annotations

import pytest

from ansible_collections.danielhreben.remote_access.plugins.module_utils import twingate as tg


def node(name, address=None, tcp=((443, 443),), groups=("Engineering",), network="Office", **kw):
    n = {
        "__typename": "NetworkResource",
        "id": "R-" + name, "name": name, "alias": None, "isVisible": True,
        "isBrowserShortcutEnabled": False, "address": {"value": address or name},
        "remoteNetwork": {"id": "N-" + network, "name": network},
        "protocols": {"allowIcmp": False,
                      "tcp": {"policy": "RESTRICTED", "ports": [{"start": a, "end": b} for a, b in tcp]},
                      "udp": {"policy": "RESTRICTED", "ports": []}},
        "access": {"edges": [{"node": {"__typename": "Group", "id": "G-" + g, "name": g}} for g in groups]},
    }
    n.update(kw)
    return n


def state(*nodes, groups=("Engineering", "Contractors"), networks=("Office",)):
    return {"resources": list(nodes),
            "groups": {g: ["G-" + g] for g in groups},
            "networks": {n: ["N-" + n] for n in networks}}


WIKI = {"name": "wiki.example.com", "tcp": [443], "groups": ["Engineering"]}
OLD = {"name": "old.example.com", "tcp": [443], "groups": ["Engineering"]}


def plan(desired, st=None, prune=False, default="Office"):
    st = st or state(node("wiki.example.com"), node("old.example.com"))
    return tg.plan(st, desired, default, prune)


class TestNormalize:
    def test_live_node_becomes_a_short_spec(self):
        assert tg.short(tg.live_full(node("wiki.example.com")), "Office") == {
            "name": "wiki.example.com", "tcp": [443], "groups": ["Engineering"]}

    def test_live_spec_keeps_what_differs_from_defaults(self):
        n = node("ssh", address="host.example.com", tcp=((22, 22), (1000, 1010)), groups=("B", "A"))
        n["protocols"]["udp"] = {"policy": "ALLOW_ALL", "ports": []}
        n["protocols"]["allowIcmp"] = True
        assert tg.short(tg.live_full(n), "Office") == {
            "name": "ssh", "address": "host.example.com", "tcp": [22, "1000-1010"],
            "udp": "all", "icmp": True, "groups": ["A", "B"]}

    def test_short_form_keeps_everything_off_default(self):
        n = node("db", tcp=(), network="Lab", alias="db.internal", isVisible=False, isBrowserShortcutEnabled=True)
        assert tg.short(tg.live_full(n), "Office") == {
            "name": "db", "alias": "db.internal", "remote_network": "Lab", "visible": False,
            "browser_shortcut": True, "groups": ["Engineering"]}

    def test_desired_fills_defaults(self):
        assert tg.normalize({"name": "a", "tcp": [443], "groups": ["X"]}, "Office") == {
            "name": "a", "address": "a", "alias": None, "remote_network": "Office", "tcp": [[443, 443]],
            "udp": [], "icmp": False, "visible": True, "browser_shortcut": False, "groups": ["X"]}

    def test_ansible_style_spec_with_none_values(self):
        spec = dict.fromkeys(tg.FIELDS)
        spec.update(name="a")
        assert tg.normalize(spec, "Office") == tg.normalize({"name": "a"}, "Office")

    def test_unknown_field_fails(self):
        with pytest.raises(tg.TwingateError, match="unknown fields"):
            tg.normalize({"name": "a", "port": 22}, "Office")

    def test_port_forms(self):
        assert tg.parse_ports([443, "80", "1000-1010"]) == [[80, 80], [443, 443], [1000, 1010]]
        assert tg.parse_ports("all") == "all"
        assert tg.parse_ports(None) == []
        assert tg.parse_ports(22) == [[22, 22]]
        assert tg.parse_ports([443, 443]) == [[443, 443]]
        with pytest.raises(tg.TwingateError):
            tg.parse_ports(["x"])

    @pytest.mark.parametrize("bad", ["x", 0, 70000, "90-80", ["all"], [True], True, "1-2-3", [1.5], {"a": 1}])
    def test_bad_ports_fail_before_any_op(self, bad):
        with pytest.raises(tg.TwingateError, match="wiki.example.com: bad port"):
            plan([dict(WIKI, tcp=bad)])

    def test_live_restricted_empty_and_deny_all_both_mean_none(self):
        a = node("a", tcp=())
        b = node("a", tcp=())
        b["protocols"]["tcp"] = {"policy": "DENY_ALL", "ports": []}
        assert tg.live_full(a) == tg.live_full(b)
        assert tg.live_full(a)["tcp"] == []


class TestPlan:
    def test_nothing_to_do(self):
        r = plan([WIKI, OLD])
        assert r["ops"] == []
        assert (r["created"], r["updated"], r["deleted"], r["unmanaged"]) == ([], [], [], [])

    def test_extra_console_resource_is_reported_not_deleted(self):
        r = plan([WIKI])
        assert r["ops"] == []
        assert r["unmanaged"] == ["old.example.com"]

    def test_prune_deletes(self):
        r = plan([WIKI], prune=True)
        assert r["ops"] == [("delete", {"id": "R-old.example.com"})]
        assert r["deleted"] == ["old.example.com"] and r["unmanaged"] == []
        assert "old.example.com" in r["before"]

    def test_prune_with_empty_list_refuses(self):
        with pytest.raises(tg.TwingateError, match="every resource"):
            plan([], prune=True)

    def test_create(self):
        r = plan([WIKI, OLD, {"name": "new.example.com", "tcp": [22], "groups": ["Contractors"]}])
        [(op, v)] = r["ops"]
        assert op == "create" and r["created"] == ["new.example.com"]
        assert v["name"] == v["address"] == "new.example.com"
        assert v["remoteNetworkId"] == "N-Office"
        assert v["groupIds"] == ["G-Contractors"]
        assert v["protocols"]["tcp"] == {"policy": "RESTRICTED", "ports": [{"start": 22, "end": 22}]}
        assert (v["isVisible"], v["isBrowserShortcutEnabled"]) == (True, False)
        assert "alias" not in v
        assert r["after"]["new.example.com"] == {"name": "new.example.com", "tcp": [22], "groups": ["Contractors"]}

    def test_no_ports_written_as_restricted_with_no_ports(self):
        # The API's ProtocolPolicy has only ALLOW_ALL and RESTRICTED; RESTRICTED
        # with no ports allows none.
        r = plan([WIKI, OLD, {"name": "new.example.com", "tcp": "all", "groups": []}])
        p = r["ops"][0][1]["protocols"]
        assert p["udp"] == {"policy": "RESTRICTED", "ports": []}
        assert p["tcp"] == {"policy": "ALLOW_ALL", "ports": []}
        assert p["allowIcmp"] is False

    def test_update_sends_only_what_changed(self):
        r = plan([dict(WIKI, tcp=[443, 80], groups=["Contractors"]), OLD])
        [(op, v)] = r["ops"]
        assert op == "update" and r["updated"] == ["wiki.example.com"]
        assert v["id"] == "R-wiki.example.com"
        assert v["addedGroupIds"] == ["G-Contractors"]
        assert v["removedGroupIds"] == ["G-Engineering"]
        assert v["protocols"]["tcp"]["ports"] == [{"start": 80, "end": 80}, {"start": 443, "end": 443}]
        assert set(v) == {"id", "addedGroupIds", "removedGroupIds", "protocols"}
        assert r["before"]["wiki.example.com"]["tcp"] == [443]
        assert r["after"]["wiki.example.com"]["tcp"] == [80, 443]

    def test_update_alias_and_clear_it(self):
        st = state(node("wiki.example.com", alias="wiki.internal"))
        [(op, v)] = plan([WIKI], st=st)["ops"]
        assert v == {"id": "R-wiki.example.com", "alias": None}
        [(op, v)] = plan([dict(WIKI, alias="w.internal")], st=st)["ops"]
        assert v == {"id": "R-wiki.example.com", "alias": "w.internal"}

    def test_live_empty_alias_equals_missing_alias(self):
        st = state(node("wiki.example.com", alias=""))
        assert plan([WIKI], st=st)["ops"] == []

    def test_moving_to_another_remote_network(self):
        st = state(node("wiki.example.com"), networks=("Office", "Lab"))
        [(op, v)] = plan([dict(WIKI, remote_network="Lab")], st=st)["ops"]
        assert v == {"id": "R-wiki.example.com", "remoteNetworkId": "N-Lab"}

    def test_unknown_group_fails_before_any_change(self):
        with pytest.raises(tg.TwingateError, match="Nobody"):
            plan([{"name": "x", "tcp": [443], "groups": ["Nobody"]}], prune=True)

    def test_unknown_remote_network_fails(self):
        with pytest.raises(tg.TwingateError, match="no such remote network 'Lab'"):
            plan([dict(WIKI, remote_network="Lab")])

    def test_duplicate_desired_names_fail(self):
        with pytest.raises(tg.TwingateError, match="twice"):
            plan([{"name": "a", "groups": []}, {"name": "a", "groups": []}])

    def test_duplicate_live_names_fail(self):
        st = state(node("wiki.example.com"), node("wiki.example.com", id="R-2"))
        with pytest.raises(tg.TwingateError, match="matched by name.*wiki.example.com"):
            plan([WIKI], st=st)

    def test_ambiguous_group_name_fails(self):
        st = state(node("wiki.example.com"))
        st["groups"]["Contractors"] = ["G-1", "G-2"]
        with pytest.raises(tg.TwingateError, match="2 groups are named 'Contractors'"):
            plan([dict(WIKI, groups=["Contractors"])], st=st)
        assert plan([WIKI], st=st)["ops"] == []  # unused: no problem

    def test_ambiguous_remote_network_fails(self):
        st = state(node("wiki.example.com"))
        st["networks"]["Office"] = ["N-1", "N-2"]
        with pytest.raises(tg.TwingateError, match="2 remote networks are named 'Office'"):
            plan([WIKI], st=st)

    def test_missing_default_remote_network_fails_only_when_needed(self):
        with pytest.raises(tg.TwingateError, match="wiki.example.com: no remote_network"):
            plan([WIKI], default=None)
        assert plan([dict(WIKI, remote_network="Office")], default=None)["ops"] == []

    def test_name_of_another_resource_type_fails(self):
        st = state(node("wiki.example.com"))
        st["others"] = {"cluster.example.com": "KubernetesResource"}
        with pytest.raises(tg.TwingateError, match="cluster.example.com is a KubernetesResource"):
            plan([WIKI, {"name": "cluster.example.com", "tcp": [443]}], st=st)
        assert plan([WIKI], st=st)["ops"] == []

    def test_service_account_access_is_left_alone(self):
        n = node("wiki.example.com")
        n["access"]["edges"].append({"node": {"__typename": "ServiceAccount", "id": "S1", "name": "ci"}})
        assert plan([WIKI], st=state(n))["ops"] == []

    def test_fingerprint_ignores_default_remote_network_option(self):
        a = plan([WIKI, OLD])
        b = plan([dict(WIKI, remote_network="Office"), dict(OLD, remote_network="Office")], default=None)
        assert a["fingerprint"] == b["fingerprint"]
        assert len(a["fingerprint"]) == 64

    def test_fingerprint_changes_when_the_console_does(self):
        a = plan([WIKI, OLD])
        b = plan([WIKI, OLD], st=state(node("wiki.example.com", isVisible=False), node("old.example.com")))
        assert a["fingerprint"] != b["fingerprint"]

    def test_resources_keep_api_order(self):
        st = state(node("z.example.com"), node("a.example.com"), node("m.example.com"))
        r = tg.plan(st, [], "Office", False)
        assert [s["name"] for s in r["resources"]] == ["z.example.com", "a.example.com", "m.example.com"]
        assert r["unmanaged"] == ["z.example.com", "a.example.com", "m.example.com"]
