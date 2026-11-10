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
import re
from urllib.parse import urlparse

from ansible.module_utils.basic import env_fallback

from ansible_collections.danielhreben.remote_access.plugins.module_utils.http_client import call

# The connection options every Twingate module takes (doc fragment: twingate).
ARGUMENT_SPEC = dict(
    network=dict(type="str", fallback=(env_fallback, ["TWINGATE_NETWORK"])),
    api_key=dict(type="str", required=True, no_log=True, fallback=(env_fallback, ["TWINGATE_API_KEY"])),
    api_url=dict(type="str"),
    validate_certs=dict(type="bool", default=True),
    timeout=dict(type="int", default=30),
)
REQUIRED_ONE_OF = [("network", "api_url")]

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


# --- The Admin API -----------------------------------------------------------

PAGE = 50

_PAGE_INFO = "pageInfo { endCursor hasNextPage }"
_GROUP_PRINCIPAL = "edges { node { __typename ... on Group { id name } } }"
_NETWORKS = "query($first: Int!, $after: String) { remoteNetworks(first: $first, after: $after) { %s edges { node { id name } } } }" % _PAGE_INFO
_GROUPS = "query($first: Int!, $after: String) { groups(first: $first, after: $after) { %s edges { node { id name } } } }" % _PAGE_INFO
_RESOURCES = (
    "query($first: Int!, $after: String) { resources(first: $first, after: $after) { %s edges { node {"
    " __typename ... on NetworkResource {"
    " id name alias isVisible isBrowserShortcutEnabled address { value } remoteNetwork { id name }"
    " protocols { allowIcmp tcp { policy ports { start end } } udp { policy ports { start end } } }"
    " access(first: $first) { %s %s } } } } } }" % (_PAGE_INFO, _PAGE_INFO, _GROUP_PRINCIPAL))
_MORE_ACCESS = (
    "query($id: ID!, $first: Int!, $after: String) { resource(id: $id) { ... on NetworkResource {"
    " access(first: $first, after: $after) { %s %s } } } }" % (_PAGE_INFO, _GROUP_PRINCIPAL))

# GraphQL variable types, strictest form: a non-null variable is accepted
# wherever the schema allows null too. A None value drops the "!".
_TYPES = {
    "id": "ID!", "name": "String!", "address": "String!", "alias": "String!", "remoteNetworkId": "ID!",
    "protocols": "ProtocolsInput!", "groupIds": "[ID!]!", "addedGroupIds": "[ID!]!", "removedGroupIds": "[ID!]!",
    "isVisible": "Boolean!", "isBrowserShortcutEnabled": "Boolean!",
}
_MUTATIONS = {"create": "resourceCreate", "update": "resourceUpdate", "delete": "resourceDelete"}


def resolve_api_url(module, network, api_key):
    """The GraphQL URL for a network. Some networks are served from a regional
    host (<network>.<region>.twingate.com) that <network>.twingate.com redirects
    to, as Twingate's Terraform provider resolves it; a POST would not survive
    that redirect. Any other final host, or a failed lookup, keeps the
    network's own host."""
    host = f"{network}.twingate.com"
    r = call(module, f"https://{host}", headers={"X-API-KEY": api_key})
    if r.status != -1:
        final = urlparse(str(r.headers.get("url") or ""))
        if final.scheme == "https" and final.hostname and final.hostname.startswith(network + ".") \
                and final.hostname.endswith(".twingate.com"):
            host = final.hostname
    return f"https://{host}/api/graphql/"


def _by_name(nodes):
    out = {}
    for n in nodes:
        out.setdefault(n["name"], []).append(n["id"])
    return out


class TwingateApi:
    def __init__(self, module, api_url, api_key):
        self.module, self.url, self.key = module, api_url, api_key

    def _post(self, text, variables, write):
        body = json.dumps({"query": text, "variables": variables or {}})
        r = call(self.module, self.url, method="POST", data=body, retry_5xx=not write,
                 headers={"X-API-KEY": self.key, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            out = json.loads(r.body) if r.body else None
        except ValueError:
            out = None
        errors = out.get("errors") if isinstance(out, dict) else None
        if errors:
            raise TwingateError("Twingate API: " + "; ".join(str(e.get("message", e)) for e in errors))
        if r.status != 200:
            detail = r.body[:300] if r.body else r.headers.get("msg", "")
            raise TwingateError(f"Twingate API ({self.url}): HTTP {r.status} {detail}".rstrip())
        if not isinstance(out, dict) or not isinstance(out.get("data"), dict):
            raise TwingateError(f"Twingate API ({self.url}) answered with something that is not JSON from the "
                                f"Admin API ({r.body[:120]!r}); if that is not your network's API, set api_url")
        return out["data"]

    def query(self, text, variables=None):
        return self._post(text, variables, write=False)

    def _pages(self, text, path, variables=None, after=None):
        nodes = []
        while True:
            conn = self.query(text, dict(variables or {}, first=PAGE, after=after))
            for key in path:
                conn = (conn or {}).get(key)
            if conn is None:
                raise TwingateError(f"Twingate API: no {'.'.join(path)} in the answer")
            nodes += [e["node"] for e in conn["edges"]]
            info = conn["pageInfo"]
            if not info["hasNextPage"]:
                return nodes
            if not info.get("endCursor") or info["endCursor"] == after:
                raise TwingateError(f"Twingate API: {'.'.join(path)} has more pages but no new cursor")
            after = info["endCursor"]

    def read_state(self):
        """Every network resource (with all its access entries), group and
        remote network, in the shape plan() takes."""
        networks = self._pages(_NETWORKS, ("remoteNetworks",))
        groups = self._pages(_GROUPS, ("groups",))
        resources = []
        for n in self._pages(_RESOURCES, ("resources",)):
            if "id" not in n:  # Kubernetes, SSH, web app... resources: not ours
                continue
            access = n["access"]
            edges = list(access["edges"])
            if access["pageInfo"]["hasNextPage"]:
                more = self._pages(_MORE_ACCESS, ("resource", "access"), {"id": n["id"]},
                                   after=access["pageInfo"]["endCursor"])
                edges += [{"node": x} for x in more]
            n["access"] = {"edges": edges}
            resources.append(n)
        return {"resources": resources, "groups": _by_name(groups), "networks": _by_name(networks)}

    def apply(self, op, variables):
        field = _MUTATIONS[op]
        decl = ", ".join(f"${k}: {_TYPES[k].rstrip('!') if v is None else _TYPES[k]}" for k, v in variables.items())
        args = ", ".join(f"{k}: ${k}" for k in variables)
        r = self._post(f"mutation({decl}) {{ r: {field}({args}) {{ ok error }} }}", variables, write=True)["r"]
        if not r or not r.get("ok"):
            what = variables.get("name") or variables.get("id")
            raise TwingateError(f"{field} {what}: {(r or {}).get('error') or 'refused'}")


def connect(module):
    """A TwingateApi for the module's connection options."""
    p = module.params
    url = p["api_url"]
    if not url:
        if not p["network"]:
            raise TwingateError("one of the following is required: network, api_url")
        if not re.match(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$", p["network"]):
            raise TwingateError(f"network {p['network']!r} is not a network name (the acme in acme.twingate.com)")
        url = resolve_api_url(module, p["network"], p["api_key"])
    return TwingateApi(module, url, p["api_key"])


def _dump(d):
    if not d:
        return ""
    try:
        import yaml
    except ImportError:
        return json.dumps(d, indent=2) + "\n"
    return yaml.safe_dump(d, sort_keys=False, default_flow_style=None, width=120)


_DONE = {"create": "created", "update": "updated", "delete": "deleted"}


def reconcile(api, desired, default_remote_network, prune, check_mode, if_match=None):
    """Read, plan, and (unless check_mode) apply. The module's result."""
    st = api.read_state()
    p = plan(st, desired, default_remote_network, prune)
    if if_match and p["fingerprint"] != if_match:
        raise TwingateError("the console's resources changed since the check; run the check again")
    if p["ops"] and not check_mode:
        names = {n["id"]: n["name"] for n in st["resources"]}
        done = []
        for i, (op, v) in enumerate(p["ops"]):
            try:
                api.apply(op, v)
            except TwingateError as e:
                rest = [f"{_DONE[o]} {x.get('name') or names[x['id']]}" for o, x in p["ops"][i:]]
                raise TwingateError(f"{e}. Done before the failure: {', '.join(done) or 'nothing'}. "
                                    f"Not done: {', '.join(rest)}.")
            done.append(f"{_DONE[op]} {v.get('name') or names[v['id']]}")
    return {
        "changed": bool(p["ops"]),
        "fingerprint": p["fingerprint"],
        "unmanaged": p["unmanaged"],
        "created": p["created"],
        "updated": p["updated"],
        "deleted": p["deleted"],
        "resources": p["resources"],
        "diff": {"before": _dump(p["before"]), "after": _dump(p["after"]),
                 "before_header": "twingate (live)", "after_header": "desired"},
    }
