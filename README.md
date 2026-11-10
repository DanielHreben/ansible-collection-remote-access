# danielhreben.remote_access

[![CI](https://github.com/DanielHreben/ansible-collection-remote-access/actions/workflows/ci.yml/badge.svg)](https://github.com/DanielHreben/ansible-collection-remote-access/actions/workflows/ci.yml)

Remote access as code: an Ansible collection that keeps **Twingate resources** and a
**Tailscale policy file** equal to what your playbook says, through each service's admin
API. Check mode and `--diff` show what would change, and a check-then-apply guard
refuses to apply over an edit someone made in the console in between.

| Module | What it does |
|---|---|
| [`twingate_resources`](plugins/modules/twingate_resources.py) | Creates, updates and (with `prune`) deletes Twingate network resources to match a list. |
| [`twingate_resource_info`](plugins/modules/twingate_resource_info.py) | Reads the resources in the same short form, e.g. to start managing an existing network. |
| [`tailscale_policy`](plugins/modules/tailscale_policy.py) | Validates and uploads a tailnet's policy file (HuJSON), comments and all. |

## Install

```bash
ansible-galaxy collection install danielhreben.remote_access
```

Needs ansible-core 2.19 or later, and Python 3.9 or later where the modules run
(usually the controller). No other Python packages; PyYAML, if present, makes the
Twingate diff easier to read.

The modules talk to the APIs from the host they run on, so run them on `localhost`
or with `delegate_to: localhost`.

## Twingate

Create an API key in the Twingate admin console (Settings, API). `twingate_resource_info`
needs **Read only**; `twingate_resources` needs **Read & Write** to make changes.

```yaml
- name: Twingate resources
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Two resources on the "Office" remote network
      danielhreben.remote_access.twingate_resources:
        network: example            # example.twingate.com
        remote_network: Office      # for resources that name none
        resources:
          - name: wiki.example.com
            tcp: [443]
            groups: [Engineering, Contractors]
          - name: build-servers
            address: 203.0.113.0/24
            tcp: [22, 8000-8100]
            icmp: true
            groups: [Engineering]
```

The API key comes from `api_key` or the `TWINGATE_API_KEY` environment variable, and the
network from `network` or `TWINGATE_NETWORK`.

### A resource

Each resource has a `name` (resources are matched by it) and only what differs from
these defaults:

| Field | Default | Meaning |
|---|---|---|
| `address` | the name | DNS name, IP address or CIDR range |
| `alias` | none | another name the resource answers to |
| `remote_network` | the module's `remote_network` | the remote network it lives on |
| `tcp`, `udp` | none | ports: a list of ports and `start-end` ranges, or `all` |
| `icmp` | `false` | allow ping |
| `visible` | `true` | shown in users' Twingate clients |
| `browser_shortcut` | `false` | show a browser shortcut |
| `groups` | none | the groups that may reach it |

These defaults are deliberately closed: a new resource allows no ports and no ICMP
until you list them, unlike the console's defaults.

Network resources in Twingate that are not in the list are reported as `unmanaged` and
left alone. With `prune: true` they are deleted. An empty list with `prune` is refused,
so a misspelt variable can't wipe the network.

### Starting from an existing network

`twingate_resource_info` returns every resource in that same short form, so you can
write it to a vars file and manage it from there:

```yaml
- danielhreben.remote_access.twingate_resource_info:
    network: example
    remote_network: Office
  register: twingate

- ansible.builtin.copy:
    dest: twingate-resources.yml
    content: "{{ {'twingate_resources': twingate.resources} | to_nice_yaml(sort_keys=false) }}"
    mode: "0644"
```

### Check, then apply

A check-mode run returns `fingerprint`, a hash of the live resources. Pass it as
`if_match` to the real run, and the apply is refused if the console changed after you
looked at the diff. [`examples/twingate.yml`](examples/twingate.yml) does the whole
check, show, ask and apply sequence.

### What it doesn't manage

Groups and their members, users, connectors, remote networks, security policies and
service accounts stay in the console. So do these resource settings, which a new
resource gets from Twingate's defaults: security policy, tags, approval mode, routing
mode, access policy, active state and per-group access settings. Kubernetes, SSH and
web app resources are ignored.

> **Status (0.1.0):** reading, comparing, exporting and deleting have run against a live
> Twingate network. Creating and updating resources have been tested against faked API
> answers only, checked against the mutations Twingate's own tools send. Run a change in
> check mode first, and please [open an issue](https://github.com/DanielHreben/ansible-collection-remote-access/issues)
> if one fails.

## Tailscale

Use an API access token (`tskey-api-...`), or better an OAuth client with the
`policy_file` scope: pass its secret (`tskey-client-...`) as `api_key` and its ID as
`oauth_client_id`. The secret is exchanged for a short-lived token on each run.

```yaml
- name: Tailnet policy
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Upload the policy file
      danielhreben.remote_access.tailscale_policy:
        src: files/tailnet-policy.hujson     # or content: "{{ lookup('template', ...) }}"
```

Before uploading, the module has Tailscale validate the policy, including the policy's
own `tests`, and stops if any fail. Your file is uploaded as written, so its comments
and layout are what the console shows. A changed comment counts as a change; Tailscale
reformatting the file when someone saves it in the console does not.

The upload carries the ETag read at the start of the run, so a console edit made in
between fails the run instead of being overwritten. A check-mode run returns `etag`;
pass it as `if_match` to stretch that guard over a check, review and apply sequence
([`examples/tailscale_policy.yml`](examples/tailscale_policy.yml)).

### Compared with `abn.tailscale.tailscale_policy`

[`abn.tailscale`](https://galaxy.ansible.com/ui/repo/published/abn/tailscale/) covers
much more of the Tailscale API (devices, keys, DNS, settings) and its `tailscale_policy`
can merge groups and tests declared on the task. Its policy is rendered in canonical form
before upload. This module instead uploads your file byte for byte, treats comment edits
as changes, and offers the `if_match` guard. Pick whichever fits how you keep the file.

## Environment variables

| Variable | Option |
|---|---|
| `TWINGATE_API_KEY` | `api_key` of the Twingate modules |
| `TWINGATE_NETWORK` | `network` of the Twingate modules |
| `TAILSCALE_API_KEY`, then `TAILSCALE_OAUTH_CLIENT_SECRET` | `api_key` of `tailscale_policy` |
| `TAILSCALE_OAUTH_CLIENT_ID` | `oauth_client_id` |
| `TAILSCALE_TAILNET` | `tailnet` |
| `TAILSCALE_BASE_URL` | `api_url` |

The Tailscale names are the ones Tailscale's Terraform provider reads.

## Rate limits and retries

A request answered with HTTP 429 is retried after the wait the API asks for (at most
120 seconds, at most 5 tries). Reads are also retried on HTTP 502, 503 and 504 and on
connection errors. A change is never retried after a server error, since it may have
been applied; if one fails partway, the error says which changes were already made.

## Development

The repository root is the collection root, so check it out as
`ansible_collections/danielhreben/remote_access`, then:

```bash
ansible-test units --venv --requirements
```

```bash
ansible-test sanity --venv --requirements
```

The unit tests fake both APIs; nothing in CI talks to a real Twingate network or tailnet.

## License

GNU General Public License v3.0 or later. See [LICENSE](LICENSE).
