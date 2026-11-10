# Copyright (c) 2026 Daniel Hreben
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP for the API modules: fetch_url plus retries on rate limits and outages.

A request is retried on HTTP 429 (the server refused it before doing anything),
and, only when the caller says the request is safe to repeat, on 502/503/504 and
once on a connection error. A write that hit a 5xx may have been applied, so it
is not retried.
"""
from __future__ import annotations

import time

from ansible.module_utils.urls import fetch_url

TRIES = 5
BACKOFF = (2, 5, 15, 30)
MAX_WAIT = 120
RETRY_5XX = frozenset((502, 503, 504))


class Response:
    """An HTTP answer: status (-1 for a connection error), headers as fetch_url
    reports them (lower-case names, plus its own ``msg``), the body as text, and
    the URL that answered (after any redirects)."""

    def __init__(self, status, headers, body, url=None):
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    def __repr__(self):
        return f"Response({self.status}, {self.body[:80]!r})"


def _text(raw):
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _wait(headers, attempt):
    try:
        seconds = int(str(headers.get("retry-after", "")).strip())
    except ValueError:
        return BACKOFF[attempt]
    return max(0, min(seconds, MAX_WAIT))


def call(module, url, method="GET", data=None, headers=None, retry_5xx=True, sleep=None,
         unredirected_headers=None):
    """Send one request, retrying as described above. Never raises for an HTTP
    status: the caller reads ``Response.status``. ``unredirected_headers`` are
    not sent on to a host the request is redirected to."""
    sleep = sleep or time.sleep
    attempt = 0
    while True:
        kw = {"unredirected_headers": unredirected_headers} if unredirected_headers else {}
        resp, info = fetch_url(module, url, data=data, headers=headers, method=method,
                               timeout=module.params["timeout"], **kw)
        status = info.get("status", -1)
        # On an HTTP error fetch_url returns the HTTPError itself, already read:
        # the body is in info["body"].
        if "body" in info:
            raw = info["body"]
        else:
            raw = resp.read() if resp is not None else None
        final = resp.geturl() if resp is not None and hasattr(resp, "geturl") else info.get("url", url)
        retry = status == 429 or (retry_5xx and (status in RETRY_5XX or (status == -1 and attempt == 0)))
        if not retry or attempt == TRIES - 1:
            return Response(status, info, _text(raw), final)
        sleep(_wait(info, attempt) if status == 429 else BACKOFF[attempt])
        attempt += 1
