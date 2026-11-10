#!/usr/bin/python
# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

DOCUMENTATION = r"""
module: tailscale_policy
short_description: Keep a Tailscale tailnet's policy file equal to your own HuJSON
version_added: 0.1.0
author: Daniel Hreben (@DanielHreben)
description:
  - Reads the tailnet's live policy file (HuJSON, comments kept) and its ETag,
    has Tailscale validate the desired policy (its syntax and the policy's own
    C(tests)), and uploads the desired text when its content or its comments
    differ from the live file.
  - Your file is uploaded exactly as written, so its comments and layout are
    what the admin console shows. Formatting alone is not a change, because
    Tailscale reformats the file whenever it is saved in the console; a changed
    comment is a change.
  - The upload carries the ETag read at the start of the run, so an edit made in
    the console in between fails the run instead of being overwritten. Pass the
    RV(etag) of a check-mode run as O(if_match) to stretch that protection over a
    whole check-then-apply sequence (see the examples).
  - The whole policy file is replaced, so a mistake affects every device in the
    tailnet. Put C(tests) in the policy; Tailscale refuses a policy whose tests
    fail, and this module stops before uploading it.
extends_documentation_fragment:
  - danielhreben.remote_access.tailscale
  - ansible.builtin.action_common_attributes
attributes:
  check_mode:
    support: full
    details: Reads the live policy and validates the desired one; uploads nothing.
  diff_mode:
    support: full
    details: Shows the live policy against the desired one when they differ.
  platform:
    platforms: all
options:
  src:
    description:
      - Path of the desired policy file (HuJSON), on the host the module runs on.
      - Exactly one of O(src) and O(content) is required.
    type: path
  content:
    description:
      - The desired policy as HuJSON text, for example from a template.
      - Exactly one of O(src) and O(content) is required.
    type: str
  if_match:
    description:
      - Fail without uploading unless the live policy's ETag is still this one.
      - Use the RV(etag) a check-mode run returned.
    type: str
seealso:
  - name: Tailscale policy file syntax
    description: What a policy file can hold, including C(tests).
    link: https://tailscale.com/kb/1337/policy-syntax
  - name: Tailscale OAuth clients
    description: An OAuth client needs the C(policy_file) scope.
    link: https://tailscale.com/kb/1215/oauth-clients
"""

EXAMPLES = r"""
- name: Upload the policy file
  danielhreben.remote_access.tailscale_policy:
    src: files/tailnet-policy.hujson
    api_key: "{{ lookup('ansible.builtin.env', 'TAILSCALE_API_KEY') }}"
  delegate_to: localhost

- name: Use an OAuth client (needs the policy_file scope) and a templated policy
  danielhreben.remote_access.tailscale_policy:
    content: "{{ lookup('ansible.builtin.template', 'tailnet-policy.hujson.j2') }}"
    oauth_client_id: "{{ tailscale_oauth_client_id }}"
    api_key: "{{ tailscale_oauth_client_secret }}"
  delegate_to: localhost

# Check, show the diff, ask, then upload only if nobody changed the policy since.
- name: Compare the live policy with ours
  danielhreben.remote_access.tailscale_policy:
    src: files/tailnet-policy.hujson
  check_mode: true
  diff: true
  register: plan
  delegate_to: localhost

- name: Confirm
  ansible.builtin.pause:
    prompt: Upload this policy? Press Enter, or Ctrl-C then A to stop
  when: plan.changed

- name: Upload, only if the live policy is still the one compared
  danielhreben.remote_access.tailscale_policy:
    src: files/tailnet-policy.hujson
    if_match: "{{ plan.etag }}"
  when: plan.changed
  delegate_to: localhost
"""

RETURN = r"""
etag:
  description: The live policy's ETag after the run (the new one after an upload).
  type: str
  returned: always
  sample: '"6a7b3a1fd0c1fbd1f5e9"'
"""

from ansible.module_utils.basic import AnsibleModule

from ansible_collections.danielhreben.remote_access.plugins.module_utils.tailscale import (
    ARGUMENT_SPEC,
    PolicyError,
    connect,
    reconcile,
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            ARGUMENT_SPEC,
            src=dict(type="path"),
            content=dict(type="str"),
            if_match=dict(type="str"),
        ),
        mutually_exclusive=[("src", "content")],
        required_one_of=[("src", "content")],
        supports_check_mode=True,
    )
    p = module.params
    try:
        if p["src"] is not None:
            try:
                with open(p["src"], encoding="utf-8") as f:
                    desired = f.read()
            except (OSError, UnicodeDecodeError) as e:
                raise PolicyError(f"reading {p['src']}: {e}")
        else:
            desired = p["content"]
        result = reconcile(connect(module), desired, module.check_mode, p["if_match"])
    except PolicyError as e:
        module.fail_json(msg=str(e))
    if not module._diff:
        result.pop("diff", None)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
