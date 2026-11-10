#!/usr/bin/python
# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

DOCUMENTATION = r"""
module: twingate_resource_info
short_description: Read a Twingate network's resources, groups and remote networks
version_added: 0.1.0
author: Daniel Hreben (@DanielHreben)
description:
  - Reads every network resource through the Twingate Admin API and returns it
    in the short form that M(danielhreben.remote_access.twingate_resources)
    takes, so the result can be written straight to a vars file to start
    managing an existing network.
  - Also returns the names of the groups and remote networks.
  - Kubernetes, SSH and web app resources are not included.
extends_documentation_fragment:
  - danielhreben.remote_access.twingate
  - ansible.builtin.action_common_attributes
attributes:
  check_mode:
    support: full
  diff_mode:
    support: N/A
  platform:
    platforms: all
options:
  remote_network:
    description:
      - Resources on this remote network leave it out of RV(resources), as
        M(danielhreben.remote_access.twingate_resources) does for its own
        O(danielhreben.remote_access.twingate_resources#module:remote_network) option.
    type: str
seealso:
  - module: danielhreben.remote_access.twingate_resources
"""

EXAMPLES = r"""
- name: Read the resources
  danielhreben.remote_access.twingate_resource_info:
    network: example
    remote_network: Office
  register: twingate
  delegate_to: localhost

- name: Save them as a vars file for twingate_resources
  ansible.builtin.copy:
    dest: twingate-resources.yml
    content: "{{ {'twingate_resources': twingate.resources} | to_nice_yaml(sort_keys=false) }}"
    mode: "0644"
  delegate_to: localhost
"""

RETURN = r"""
resources:
  description:
    - The network resources in the short form of
      M(danielhreben.remote_access.twingate_resources)'s
      O(danielhreben.remote_access.twingate_resources#module:resources) option
      (defaults left out), in the order the API returns them.
  type: list
  elements: dict
  returned: always
  sample: [{name: wiki.example.com, tcp: [443], groups: [Engineering]}]
groups:
  description: The names of all groups, sorted.
  type: list
  elements: str
  returned: always
  sample: [Contractors, Engineering, Everyone]
remote_networks:
  description: The names of all remote networks, sorted.
  type: list
  elements: str
  returned: always
  sample: [Office]
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.danielhreben.remote_access.plugins.module_utils.twingate import (
    ARGUMENT_SPEC,
    REQUIRED_ONE_OF,
    TwingateError,
    connect,
    live_full,
    short,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(ARGUMENT_SPEC, remote_network=dict(type="str")),
        required_one_of=REQUIRED_ONE_OF,
        supports_check_mode=True,
    )
    try:
        st = connect(module).read_state()
    except TwingateError as e:
        module.fail_json(msg=str(e))
    default = module.params["remote_network"]
    module.exit_json(
        changed=False,
        resources=[short(live_full(n), default) for n in st["resources"]],
        groups=sorted(st["groups"]),
        remote_networks=sorted(st["networks"]),
    )


if __name__ == "__main__":
    main()
