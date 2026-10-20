# Changelog

All notable changes to the `dfo-spec-kit-client-contract` Python package will be documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Package scaffolding (uv, ruff, ty, pytest).
- `served/openapi.json`, the composed dfo-apis app's OpenAPI document, and `served/SOURCE.json`, naming dfo-apis commit `8408f1f95c622223f8fa6e9ba55826ee8e22a852`.
- `strict-fields.json` and its schema: 25 members that stay required and non-nullable, and the answer kinds, part statuses and operation statuses the clients branch on.
- `actions.json` and its schema: the caller's next step for each of the 101 active refusal codes on `problem_details` or `no_data_reason`, with the gap kind of each no_data reason.
- `scenario.schema.json`, the scenario record, and two recorded scenarios: a place that matches two countries, and a fetch of a spec whose sentence the client holds, with its Parquet body.
- `dataset-ref.schema.json`, the frozen `DatasetRef` format, with accepted and rejected examples.
- The client rules and their vectors: the credentials path and file format, the key lookup order, the key format check, and the cache directory and layout.

[unreleased]: https://github.com/DevelopmentFinanceObservatory/spec-kit/commits/main/python
