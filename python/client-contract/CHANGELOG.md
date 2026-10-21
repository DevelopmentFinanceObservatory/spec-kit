# Changelog

All notable changes to the `dfo-spec-kit-client-contract` Python package will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Package scaffolding (uv, ruff, ty, pytest).
- `served/openapi.json`, the composed dfo-apis app's OpenAPI document, and `served/SOURCE.json`, naming dfo-apis commit `8408f1f95c622223f8fa6e9ba55826ee8e22a852`.
- `strict-fields.json` and its schema: 25 members that stay required and non-nullable, and the answer kinds, part statuses and operation statuses the clients branch on.
- `actions.json` and its schema: the caller's next step for each of the 101 active refusal codes on `problem_details` or `no_data_reason`, with the gap kind of each no_data reason.
- The status fallback for a refusal whose code has no row: 400 and 422 to `InvalidRequestError`, 401 to `AuthenticationError`, 403 to `PermissionDeniedError`, 404 and 410 to `NotFoundError`, 408 to `DfoTimeoutError`, 429 to `RateLimitError`, and any other 4xx or 5xx to `InvalidRequestError` or `ServerError`.
- `scenario.schema.json`, the scenario record, and 56 scenarios: 21 recorded against dev at dfo-apis `8408f1f95`, `7175fe98e` and `687d48bf1`, and 35 synthetic, each derived from a recorded one by a one-line edit, with 15 binary bodies totalling 131,004 bytes. The scenarios cover the several-part choice entries, the choice refusals, the timed_out, unsupported and unknown gaps, an unknown answer kind, the `SearchWarning` rule, the stacked dataset's `part` column, the frame layout, a strict fetch's `DataWarning`, a spec fetch that rebinds one of two entity roles and keeps the other, one that drops a role bound to null, a twice cut-off build, a refused build, a digest mismatch, an identity resubmit answering another spec, and a ref whose stored file expired, rebuilt at the same version, at a newer one, or refused. Six more are defined and wait for a served document that declares the build's 202 or the file selector: `mint-async-build`, `operation-resume`, `fetch-spec-async-build`, `fetch-spec-poll-rate-limited`, `fetch-spec-operation-failed-resubmitted` and `fetch-ref-expired-file-selector`.
- `client-codes.json` and its schema: the 16 codes a client sets on the errors it raises itself, each with its error class and condition, disjoint from `actions.json` and `registries/codes.json`, and the `client_codes()` and `client_codes_schema()` accessors. Every scenario error the client raises itself carries its code, and `refusal_without_code` names a refusal whose body carries no code.
- `dataset-ref.schema.json`, the frozen `DatasetRef` format, with accepted and rejected examples.
- `tools/capture_client_contract.py`, which captures `served/openapi.json` from a dfo-apis checkout in process and records the scenarios against a base URL, dev by default, or with `record --offline` derives their outcomes again from the records with no request. A scenario that needs a route or status the served document lacks waits until the document declares it.
- `tools/validate_client_contract.py`, which checks the contract against its schemas, the served document and `registries/codes.json`, checks `client-codes.json` against them, and checks each scenario's `expect` against the outcomes its exchanges, `actions.json` and `client-codes.json` give, each Search request against the input its steps derive, each build's `input.scope` against the narrowing its step names over the spec's bound roles, each build's options against its layout, and each download against its artifact's digest. The outcomes include each long build's operation id, poll waits and resubmissions, and a `VersionUnavailableError`'s newest version.
- The client rules and their vectors: the credentials path and file format, the key lookup order, the key format check, and the cache directory and layout.

[unreleased]: https://github.com/DevelopmentFinanceObservatory/spec-kit/commits/main/python
