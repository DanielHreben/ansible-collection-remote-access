#!/usr/bin/python
# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

DOCUMENTATION = r"""
module: twingate_resources
short_description: Keep a Twingate network's resources equal to a list
version_added: 0.1.0
author: Daniel Hreben (@DanielHreben)
description:
  - Reads every network resource through the Twingate Admin API (address,
    protocols and ports, visibility, alias, remote network, and which groups
    may reach it), then creates and updates resources to match O(resources).
  - Resources are matched by name. A resource only in Twingate is reported in
    RV(unmanaged) and left alone, unless O(prune) is set.
  - Check mode reads only. With C(--diff) it shows, in the short form of
    O(resources), every resource that would change.
  - Returns RV(fingerprint), a hash of the live resources. Pass the fingerprint
    of a check-mode run as O(if_match) to refuse an apply when the console
    changed after the check (see the examples).
  - Left as they are, and not managed, are groups and their members, users,
    connectors, remote networks, security policies, service accounts' access,
    and these resource settings (security policy, tags, approval mode, routing
    mode, access policy, active state and per-group access settings). A new
    resource gets Twingate's defaults for them.
  - Only network resources are handled. Kubernetes, SSH and web app resources
    are ignored, and listing a name that belongs to one of them is an error.
  - If a change fails partway, the error lists the changes already made, the
    one that failed (or whose outcome is unknown, after a server error or a
    lost connection), and those not attempted.
  - Creating, updating and deleting resources have run against a live network
    (as of 0.1.1). Aliases, browser shortcuts and moving a resource to another
    remote network have been tested against faked API answers only. Every query
    and mutation is validated against Twingate's published GraphQL schema. Try a
    change in check mode first.
extends_documentation_fragment:
  - danielhreben.remote_access.twingate
  - ansible.builtin.action_common_attributes
attributes:
  check_mode:
    support: full
    details: Reads the live resources and reports what would change.
  diff_mode:
    support: full
    details: Shows the resources that would change, before and after.
  platform:
    platforms: all
options:
  remote_network:
    description:
      - The remote network of resources that name none.
      - Required only if some resource in O(resources) has no C(remote_network).
        Resources on this network leave it out of RV(resources).
    type: str
  resources:
    description:
      - The desired resources. Every network resource in Twingate not listed
        here is reported in RV(unmanaged), or deleted with O(prune).
    type: list
    elements: dict
    default: []
    suboptions:
      name:
        description: The resource's name; resources are matched by it.
        type: str
        required: true
      address:
        description: A DNS name, IP address or CIDR range. Defaults to the name.
        type: str
      alias:
        description: An alias the resource can also be reached by.
        type: str
      remote_network:
        description: The resource's remote network. Defaults to O(remote_network).
        type: str
      tcp:
        description:
          - TCP ports users may reach, as a list of ports and C(start-end)
            ranges, or C(all). Leave out for none.
        type: raw
      udp:
        description: UDP ports, as for C(tcp). Leave out for none.
        type: raw
      icmp:
        description: Whether ICMP (ping) is allowed.
        type: bool
        default: false
      visible:
        description: Whether users see the resource in their Twingate client.
        type: bool
        default: true
      browser_shortcut:
        description: Whether the client shows a browser shortcut for it.
        type: bool
        default: false
      groups:
        description: Names of the groups that may reach the resource.
        type: list
        elements: str
        default: []
  prune:
    description:
      - Delete network resources that are not in O(resources).
      - Refused when O(resources) is empty, which would delete every resource.
    type: bool
    default: false
  if_match:
    description:
      - Fail without changing anything unless the live resources' fingerprint
        is still this one. Use the RV(fingerprint) a check-mode run returned.
    type: str
seealso:
  - module: danielhreben.remote_access.twingate_resource_info
  - name: Twingate Admin API
    description: API keys, permissions and rate limits.
    link: https://www.twingate.com/docs/api-overview
"""

EXAMPLES = r"""
- name: Two resources on the "Office" remote network
  danielhreben.remote_access.twingate_resources:
    network: example
    api_key: "{{ lookup('ansible.builtin.env', 'TWINGATE_API_KEY') }}"
    remote_network: Office
    resources:
      - name: wiki.example.com
        tcp: [443]
        groups: [Engineering, Contractors]
      - name: build-servers
        address: 203.0.113.0/24
        tcp: [22, 8000-8100]
        icmp: true
        groups: [Engineering]
  delegate_to: localhost

# Check, show the diff, ask, then apply only if nobody changed the console since.
- name: Compare Twingate with the list
  danielhreben.remote_access.twingate_resources:
    network: example
    remote_network: Office
    resources: "{{ twingate_resources }}"
    prune: true
  check_mode: true
  diff: true
  register: plan
  delegate_to: localhost

- name: Confirm
  ansible.builtin.pause:
    prompt: Apply these changes to Twingate? Press Enter, or Ctrl-C then A to stop
  when: plan.changed

- name: Apply, only if the console is still as compared
  danielhreben.remote_access.twingate_resources:
    network: example
    remote_network: Office
    resources: "{{ twingate_resources }}"
    prune: true
    if_match: "{{ plan.fingerprint }}"
  when: plan.changed
  delegate_to: localhost
"""

RETURN = r"""
fingerprint:
  description: A hash of the live network resources, as read before any change.
  type: str
  returned: always
  sample: 3f1c0e9b6d4a8f2e7c5b1a9d0e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f
unmanaged:
  description: Network resources in Twingate that are not in O(resources) (empty with O(prune)).
  type: list
  elements: str
  returned: always
  sample: [old.example.com]
created:
  description: Names of the resources created (or that would be, in check mode).
  type: list
  elements: str
  returned: always
updated:
  description: Names of the resources updated (or that would be).
  type: list
  elements: str
  returned: always
deleted:
  description: Names of the resources deleted (or that would be).
  type: list
  elements: str
  returned: always
resources:
  description:
    - The live network resources as read before any change, in the short form
      of O(resources) and in the order the API returns them.
  type: list
  elements: dict
  returned: always
  sample: [{name: wiki.example.com, tcp: [443], groups: [Engineering]}]
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.danielhreben.remote_access.plugins.module_utils.twingate import (
    ARGUMENT_SPEC,
    REQUIRED_ONE_OF,
    TwingateError,
    connect,
    reconcile,
)

RESOURCE = dict(
    name=dict(type="str", required=True),
    address=dict(type="str"),
    alias=dict(type="str"),
    remote_network=dict(type="str"),
    tcp=dict(type="raw"),
    udp=dict(type="raw"),
    icmp=dict(type="bool", default=False),
    visible=dict(type="bool", default=True),
    browser_shortcut=dict(type="bool", default=False),
    groups=dict(type="list", elements="str", default=[]),
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            ARGUMENT_SPEC,
            remote_network=dict(type="str"),
            resources=dict(type="list", elements="dict", default=[], options=RESOURCE),
            prune=dict(type="bool", default=False),
            if_match=dict(type="str"),
        ),
        required_one_of=REQUIRED_ONE_OF,
        supports_check_mode=True,
    )
    p = module.params
    try:
        result = reconcile(connect(module), p["resources"], p["remote_network"], p["prune"],
                           module.check_mode, p["if_match"])
    except TwingateError as e:
        module.fail_json(msg=str(e))
    if not module._diff or not result["changed"]:
        result.pop("diff")
    module.exit_json(**result)


if __name__ == "__main__":
    main()
