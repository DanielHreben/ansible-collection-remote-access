# Changelog

All notable changes to `danielhreben.remote_access`. Versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-11-10

Documentation only; the code is the same as 0.1.0.

### Changed

- Every kind of write has now run against a live service: a Twingate resource created,
  updated (ports and ranges, `all`, UDP, ICMP, adding and removing a group) and deleted
  with `prune`, and a Tailscale policy file uploaded. The "faked API answers only" note
  now covers just aliases, browser shortcuts and moving a resource to another remote
  network.

## [0.1.0] - 2026-11-10

First release.

### Added

- `twingate_resources`: keep a Twingate network's resources equal to a list, with check
  mode, diff, `prune`, and a fingerprint guard for check-then-apply.
- `twingate_resource_info`: read the resources in the same short form, plus group and
  remote network names.
- `tailscale_policy`: validate and upload a tailnet policy file (HuJSON) as written, with
  check mode, diff, an ETag guard, and API-token or OAuth-client credentials.

### Known limitations

- Twingate create, update and delete are tested against faked API answers only.
