# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations


class ModuleDocFragment:
    # The connection options of every Tailscale module.
    DOCUMENTATION = r"""
options:
  api_key:
    description:
      - An API access token (C(tskey-api-...)), or an OAuth client secret
        (C(tskey-client-...)) together with O(oauth_client_id).
      - If not set, the environment variable E(TAILSCALE_API_KEY) is used, then
        E(TAILSCALE_OAUTH_CLIENT_SECRET).
    type: str
    required: true
  oauth_client_id:
    description:
      - The OAuth client's ID, needed when O(api_key) is an OAuth client secret.
        The secret is exchanged for a short-lived access token on every run.
      - If not set, the environment variable E(TAILSCALE_OAUTH_CLIENT_ID) is used.
    type: str
  tailnet:
    description:
      - The tailnet's ID or name. V(-) is the tailnet the credential belongs to,
        which is right unless one credential manages several tailnets.
      - If not set, the environment variable E(TAILSCALE_TAILNET) is used.
    type: str
    default: "-"
  api_url:
    description:
      - Base URL of the Tailscale API.
      - If not set, the environment variable E(TAILSCALE_BASE_URL) is used.
    type: str
    default: https://api.tailscale.com
  validate_certs:
    description: Whether to verify the API's TLS certificate.
    type: bool
    default: true
  timeout:
    description: Seconds to wait for one API request.
    type: int
    default: 30
notes:
  - The module talks to the Tailscale API from the host it runs on, usually the
    controller (C(delegate_to: localhost) or a play on C(localhost)).
  - Requests answered with HTTP 429 are retried, waiting as long as the API asks
    (at most 120 seconds, at most 5 tries). Reads are also retried on HTTP 502,
    503 and 504 and on connection errors. An upload is never retried after a
    server error, since it may have been applied.
"""
