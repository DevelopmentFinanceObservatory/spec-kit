# Changelog

All notable changes to the `dfo-spec-kit-client-contract` Python package will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Package scaffolding (uv, ruff, ty, pytest).
- `served/openapi.json`, the composed dfo-apis app's OpenAPI document, and `served/SOURCE.json`, naming dfo-apis commit `8408f1f95c622223f8fa6e9ba55826ee8e22a852`.
- `strict-fields.json` and its schema: 25 members that stay required and non-nullable, and the answer kinds, part statuses and operation statuses the clients branch on.
- `actions.json` and its schema: the caller's next step for each of the 101 active refusal codes on `problem_details` or `no_data_reason`, with the gap kind of each no_data reason.
- `scenario.schema.json`, the scenario record, and 44 scenarios: 18 recorded against dfo-apis `8408f1f95` and 26 synthetic, each derived from a recorded one by a one-line edit, with 11 Parquet bodies totalling 102,956 bytes. The scenarios cover the several-part choice entries, the choice refusals, the timed_out, unsupported and unknown gaps, an unknown answer kind, the `SearchWarning` rule and the stacked dataset's `part` column.
- `dataset-ref.schema.json`, the frozen `DatasetRef` format, with accepted and rejected examples.
- `tools/capture_client_contract.py`, which captures `served/openapi.json` from a dfo-apis checkout in process and records the scenarios against a base URL, dev by default.
- `tools/validate_client_contract.py`, which checks the contract against its schemas, the served document and `registries/codes.json`, and checks each scenario's `expect` against the outcomes its exchanges and `actions.json` give, and each Search request against the input its steps derive.
- The client rules and their vectors: the credentials path and file format, the key lookup order, the key format check, and the cache directory and layout.

[unreleased]: https://github.com/DevelopmentFinanceObservatory/spec-kit/commits/main/python
