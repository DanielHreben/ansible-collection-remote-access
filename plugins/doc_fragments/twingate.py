# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations


class ModuleDocFragment:
    # The connection options of every Twingate module.
    DOCUMENTATION = r"""
options:
  network:
    description:
      - The Twingate network's name, as in C(<network>.twingate.com).
      - The API address is looked up from it once per run, since some networks
        are served from a regional host (C(<network>.<region>.twingate.com)).
      - One of O(network) and O(api_url) is required. If not set, the
        environment variable E(TWINGATE_NETWORK) is used.
    type: str
  api_key:
    description:
      - A Twingate Admin API key. Reading needs the C(Read only) permission;
        making changes needs C(Read & Write).
      - If not set, the environment variable E(TWINGATE_API_KEY) is used.
    type: str
    required: true
  api_url:
    description:
      - The full GraphQL URL, for example C(https://acme.twingate.com/api/graphql/).
        Skips the lookup from O(network).
    type: str
  validate_certs:
    description: Whether to verify the API's TLS certificate.
    type: bool
    default: true
  timeout:
    description: Seconds to wait for one API request.
    type: int
    default: 30
notes:
  - The module talks to the Twingate Admin API from the host it runs on, usually
    the controller (a task with C(delegate_to) set to C(localhost), or a play on C(localhost)).
  - Twingate limits API requests (60 reads and 20 writes a minute by default).
    Requests answered with HTTP 429 are retried, waiting as long as the API asks
    (at most 120 seconds, at most 5 tries). Reads are also retried on HTTP 502,
    503 and 504 and on connection errors; a change is never retried after a
    server error, since it may have been applied.
"""
