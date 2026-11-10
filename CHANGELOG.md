# Changelog

All notable changes to `danielhreben.remote_access`. Versions follow
[Semantic Versioning](https://semver.org/).

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

- Twingate create and update are tested against faked API answers only.
