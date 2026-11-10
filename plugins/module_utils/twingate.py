# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Twingate resources: normalising, planning, and the Admin API (GraphQL).

Three forms of a resource:
- the *node*: a NetworkResource as the API returns it;
- the *full* form: every field filled in, comparable with ==;
- the *short* form: the module's ``resources`` option, defaults left out.
"""
from __future__ import annotations

import hashlib
import json

FIELDS = ("address", "alias", "remote_network", "tcp", "udp", "icmp", "visible", "browser_shortcut", "groups")


class TwingateError(Exception):
    pass


# --- Normalising -------------------------------------------------------------

def _port(p, where):
    bad = TwingateError(f"{where}bad port {p!r}: use a number 1-65535, a range a-b, or all")
    if isinstance(p, bool) or not isinstance(p, (int, str)):
        raise bad
    a, sep, b = str(p).strip().partition("-")
    try:
        start, end = int(a), int(b if sep else a)
    except ValueError:
        raise bad
    if not 1 <= start <= end <= 65535:
        raise bad
    return (start, end)


def parse_ports(value, name=None):
    """Ports as sorted, distinct [start, end] pairs, or "all". Accepts a port, a
    "start-end" range, "all", or a list of ports and ranges."""
    where = f"{name}: " if name else ""
    if value is None:
        return []
    if isinstance(value, str) and value.strip() == "all":
        return "all"
    items = value if isinstance(value, list) else [value]
    return [list(p) for p in sorted({_port(p, where) for p in items})]


def _ports_short(value):
    return "all" if value == "all" else [a if a == b else f"{a}-{b}" for a, b in value]


def normalize(spec, default_remote_network):
    """A desired resource with every field filled in."""
    name = spec["name"]
    unknown = set(spec) - set(FIELDS) - {"name"}
    if unknown:
        raise TwingateError(f"{name}: unknown fields {sorted(unknown)}")
    remote_network = spec.get("remote_network") or default_remote_network
    if not remote_network:
        raise TwingateError(f"{name}: no remote_network, and the remote_network option is not set")
    visible = spec.get("visible")
    return {
        "name": name,
        "address": spec.get("address") or name,
        "alias": spec.get("alias") or None,
        "remote_network": remote_network,
        "tcp": parse_ports(spec.get("tcp"), name),
        "udp": parse_ports(spec.get("udp"), name),
        "icmp": bool(spec.get("icmp")),
        "visible": True if visible is None else bool(visible),
        "browser_shortcut": bool(spec.get("browser_shortcut")),
        "groups": sorted(set(spec.get("groups") or [])),
    }


def _live_ports(protocol):
    if not protocol:
        return []
    if protocol["policy"] == "ALLOW_ALL":
        return "all"
    if protocol["policy"] == "DENY_ALL":
        return []
    return sorted([p["start"], p["end"]] for p in protocol.get("ports") or [])


def live_full(node):
    """A NetworkResource node in the full form."""
    pr = node.get("protocols") or {}
    return {
        "name": node["name"],
        "address": node["address"]["value"],
        "alias": node.get("alias") or None,
        "remote_network": node["remoteNetwork"]["name"],
        "tcp": _live_ports(pr.get("tcp")),
        "udp": _live_ports(pr.get("udp")),
        "icmp": bool(pr.get("allowIcmp")),
        "visible": bool(node["isVisible"]),
        "browser_shortcut": bool(node["isBrowserShortcutEnabled"]),
        "groups": sorted(e["node"]["name"] for e in node["access"]["edges"]
                         if e["node"].get("__typename") == "Group"),
    }


def short(full, default_remote_network):
    """A full resource in the short form of the resources option."""
    s = {"name": full["name"]}
    if full["address"] != full["name"]:
        s["address"] = full["address"]
    if full["alias"]:
        s["alias"] = full["alias"]
    if full["remote_network"] != default_remote_network:
        s["remote_network"] = full["remote_network"]
    for k in ("tcp", "udp"):
        if full[k]:
            s[k] = _ports_short(full[k])
    if full["icmp"]:
        s["icmp"] = True
    if not full["visible"]:
        s["visible"] = False
    if full["browser_shortcut"]:
        s["browser_shortcut"] = True
    s["groups"] = list(full["groups"])
    return s


def fingerprint(fulls):
    """A hash of the live resources, to tell whether the console changed."""
    data = json.dumps(sorted(fulls, key=lambda f: f["name"]), sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


# --- Planning ----------------------------------------------------------------

def _protocols(full):
    def one(v):
        if v == "all":
            return {"policy": "ALLOW_ALL", "ports": []}
        if not v:
            return {"policy": "DENY_ALL", "ports": []}
        return {"policy": "RESTRICTED", "ports": [{"start": a, "end": b} for a, b in v]}
    return {"allowIcmp": full["icmp"], "tcp": one(full["tcp"]), "udp": one(full["udp"])}


def _one_id(ids_by_name, kind, name, resource):
    ids = ids_by_name.get(name) or []
    if len(ids) > 1:
        raise TwingateError(f"{resource}: {len(ids)} {kind}s are named {name!r}; rename all but one in the console")
    return ids[0] if ids else None


def plan(state, desired, default_remote_network, prune):
    """What it takes to make the live resources match ``desired``.

    ``state`` is ``{"resources": [node, ...], "groups": {name: [id, ...]},
    "networks": {name: [id, ...]}}``. Raises TwingateError, before anything is
    planned, for any problem in ``desired`` or an ambiguity in the console."""
    nodes = state["resources"]
    names = [n["name"] for n in nodes]
    duplicates = sorted({x for x in names if names.count(x) > 1})
    if duplicates:
        raise TwingateError("resources are matched by name, and more than one resource is named "
                            f"{', '.join(map(repr, duplicates))}; rename them in the console")
    if prune and not desired:
        raise TwingateError("prune with an empty resources list would delete every resource; refusing")
    live = {n["name"]: n for n in nodes}
    have = {n["name"]: live_full(n) for n in nodes}

    want = {}
    for spec in desired:
        full = normalize(spec, default_remote_network)
        if full["name"] in want:
            raise TwingateError(f"{full['name']} is listed twice")
        want[full["name"]] = full

    network_id, group_id = {}, {}
    for name, full in want.items():
        missing = [g for g in full["groups"] if not state["groups"].get(g)]
        if missing:
            raise TwingateError(f"{name}: no such groups {missing}")
        for g in full["groups"]:
            group_id[g] = _one_id(state["groups"], "group", g, name)
        rn = full["remote_network"]
        network_id[rn] = _one_id(state["networks"], "remote network", rn, name)
        if network_id[rn] is None:
            raise TwingateError(f"{name}: no such remote network {rn!r}")

    out = {"ops": [], "created": [], "updated": [], "deleted": [], "before": {}, "after": {}}
    for name, f in want.items():
        if name not in have:
            v = {"name": name, "address": f["address"], "remoteNetworkId": network_id[f["remote_network"]],
                 "protocols": _protocols(f), "groupIds": [group_id[g] for g in f["groups"]],
                 "isVisible": f["visible"], "isBrowserShortcutEnabled": f["browser_shortcut"]}
            if f["alias"]:
                v["alias"] = f["alias"]
            out["ops"].append(("create", v))
            out["created"].append(name)
            out["after"][name] = short(f, default_remote_network)
            continue
        h = have[name]
        if f == h:
            continue
        v = {"id": live[name]["id"]}
        if f["address"] != h["address"]:
            v["address"] = f["address"]
        if f["alias"] != h["alias"]:
            v["alias"] = f["alias"]
        if f["remote_network"] != h["remote_network"]:
            v["remoteNetworkId"] = network_id[f["remote_network"]]
        if (f["tcp"], f["udp"], f["icmp"]) != (h["tcp"], h["udp"], h["icmp"]):
            v["protocols"] = _protocols(f)
        if f["visible"] != h["visible"]:
            v["isVisible"] = f["visible"]
        if f["browser_shortcut"] != h["browser_shortcut"]:
            v["isBrowserShortcutEnabled"] = f["browser_shortcut"]
        added = [group_id[g] for g in f["groups"] if g not in h["groups"]]
        live_groups = {e["node"]["name"]: e["node"]["id"] for e in live[name]["access"]["edges"]
                       if e["node"].get("__typename") == "Group"}
        removed = [live_groups[g] for g in h["groups"] if g not in f["groups"]]
        if added:
            v["addedGroupIds"] = added
        if removed:
            v["removedGroupIds"] = removed
        out["ops"].append(("update", v))
        out["updated"].append(name)
        out["before"][name] = short(h, default_remote_network)
        out["after"][name] = short(f, default_remote_network)

    unmanaged = [n for n in names if n not in want]
    if prune:
        for name in unmanaged:
            out["ops"].append(("delete", {"id": live[name]["id"]}))
            out["deleted"].append(name)
            out["before"][name] = short(have[name], default_remote_network)
    out["unmanaged"] = [] if prune else unmanaged
    out["fingerprint"] = fingerprint(list(have.values()))
    out["resources"] = [short(have[n], default_remote_network) for n in names]
    return out
