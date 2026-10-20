# dfo-spec-kit-client-contract (Python)

This package holds the contract the DFO clients are built to: the Python client `development-finance` and the TypeScript client `@developmentfinance/client`. The clients generate their wire types, strict checks and error maps from these files and replay the scenarios. dfo-apis CI checks its app against the same files, so a server change that breaks a client fails there first. The files below are normative; where this README and the files disagree, the files win and the disagreement is a bug. See the [repo README](../../README.md) for how this package fits with the rest of `dfo-spec-kit`.

The import name is `dfo_client_contract`. It depends on the standard library only.

## Installation

This package uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run ty check src/dfo_client_contract
```

## Contents

`src/dfo_client_contract/` ships with the package. Each schema's `$id` is its path under `https://raw.githubusercontent.com/DevelopmentFinanceObservatory/spec-kit/main/python/client-contract/src/dfo_client_contract/`, as the kit's other schemas are, because the kit has no schema-hosting service.

| File                        | Holds                                                                                 |
| --------------------------- | ------------------------------------------------------------------------------------- |
| `served/openapi.json`       | The composed dfo-apis app's OpenAPI document, which both clients generate types from  |
| `served/SOURCE.json`        | The dfo-apis repository and commit `served/openapi.json` was captured from            |
| `strict-fields.json`        | The response members the clients act on                                               |
| `strict-fields.schema.json` | The shape of `strict-fields.json`                                                     |
| `actions.json`              | The caller's next step for each refusal code, and the gap kind of each no_data reason |
| `actions.schema.json`       | The shape of `actions.json`                                                           |
| `scenario.schema.json`      | One scenario record                                                                   |
| `scenarios/`                | The scenario records, and the binary response bodies they name                        |
| `dataset-ref.schema.json`   | The frozen `DatasetRef` format                                                        |
| `refs/`                     | Accepted and rejected `DatasetRef` examples                                           |
| `rules/`                    | The client rules, one vector file each, with `rules.schema.json` and the rules' text  |

| Function          | Returns                                                     |
| ----------------- | ----------------------------------------------------------- |
| `openapi()`       | `served/openapi.json`, parsed                               |
| `served_source()` | `served/SOURCE.json`, parsed                                |
| `strict_fields()` | `strict-fields.json`, parsed                                |
| `actions()`       | `actions.json`, parsed                                      |
| `scenarios()`     | Every scenario record; raises `RuntimeError` when none load |
| `ref_examples()`  | Every `DatasetRef` example; raises `RuntimeError` when none |
| `rule(name)`      | The vector file for one of `RULES`                          |
| `contract_dir()`  | The installed contract directory                            |
| `*_schema()`      | Each schema, parsed                                         |

## Served document

`served/openapi.json` is `app.openapi()` of the composed dfo-apis app, every surface mounted. `served/SOURCE.json` names the commit it came from as `{format_version, repository, commit, captured_from}`. It names the closed runtime models, `Manifest`, `ArtifactEntryModel`, every Search part model and `ProblemDetails` among them, which the per-surface schema routes leave open. Both clients generate wire types from it and parse tolerantly regardless of its `additionalProperties: false`: an unknown member or enum value passes through.

## Strict fields

`strict-fields.json` lists the members a client cannot act without. Each pointer names one property of one component schema, `/components/schemas/<Schema>/properties/<member>`. A `required` entry stays in its schema's `required` list and admits no null, so a client raises `ContractError` when one is missing or null. An `enums` entry keeps every listed value, the values a client branches on: the answer kinds, the part statuses and the operation statuses. A client still accepts a value no entry lists.

The required members are each part's `status` and its payload (`spec`, `candidates`, `no_data`, `unsupported`), `Resolution.parts`, `CandidateSet.specs`, `NoData.reason`, `Unsupported.code`, `Spec.spec_id` and `definition`, `SpecDefinition.spec_id`, the manifest's `definition`, `definition.spec_id`, `content_version` and `artifacts`, each artifact's `artifact_digest`, `rendering` and `url`, and the operation resource's `operation`, `operation_id` and `status`. The operation's `poll` is nullable in the served document, so a client builds the poll path from `operation_id`.

## Actions

`actions.json` has one row per active refusal code in `registries/codes.json` that reaches a problem document (`problem_details`) or a no_data part's `reason` (`no_data_reason`), sorted by code. `action` is the caller's next step, and each client maps it to one error class.

| `action`              | Error class               | The caller                                       |
| --------------------- | ------------------------- | ------------------------------------------------ |
| `fix_request`         | `InvalidRequestError`     | Changes the request                              |
| `check_id`            | `NotFoundError`           | Checks the spec, version, operation or other id  |
| `choose`              | `IncompleteAnswerError`   | Chooses among the gap's options                  |
| `ask_data_question`   | `IncompleteAnswerError`   | Asks the question another way                    |
| `take_newest_version` | `VersionUnavailableError` | Takes the newest version                         |
| `log_in`              | `AuthenticationError`     | Logs in                                          |
| `request_scope`       | `PermissionDeniedError`   | Asks for the permission the call needs           |
| `wait`                | `RateLimitError`          | Waits, then retries                              |
| `wait_for_reset`      | `QuotaExceededError`      | Waits for the quota to reset                     |
| `report`              | `ServerError`             | Reports the trace id, after the client's retries |
| `upgrade`             | `ContractError`           | Upgrades the client                              |

A code that maps to `no_data_reason` also carries `gap_kind`, the kind of gap a no_data part with that reason becomes: `choice`, `no_data`, `not_understood` or `unsupported`. `choose` goes with `choice` and only with it. `choose` and `ask_data_question` name a next step inside an answer, so a code that also reaches a problem document takes another action. A client maps an unknown code on its HTTP status, and a no_data reason with no row becomes a `no_data` gap.

An answer's gaps follow from its parts. A candidates part is a `choice` gap, its options the candidates. A no_data part is a gap of its reason's `gap_kind`, its options the no_data `options`, and of kind `timed_out` instead when the answer's `diagnostics.timed_out` is true. An unsupported part is an `unsupported` gap. A part whose status a client does not know is an `unknown` gap.

## Scenarios

A scenario is one client-level call, the HTTP exchanges it makes, and the outcome each step reaches. Each record is one file, `scenarios/<name>.json`, validated by `scenario.schema.json`.

| Member           | Holds                                                                                                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `format_version` | `1`                                                                                                                                                                 |
| `name`           | The kebab-case name, equal to the file name's stem                                                                                                                  |
| `description`    | One or two sentences on what the scenario proves                                                                                                                    |
| `origin`         | `recorded`, with the `base_url`, the deployed `dfo_commit` and `recorded_at`; or `synthetic`, with the recorded scenario it is `derived_from` and a one-line `edit` |
| `call`           | The steps, in order: `search`; `choose`; `fetch` of the previous answer, of a `spec` with `window`, `entities` and `facets`, or of a `ref`; `mint`; `result`        |
| `exchanges`      | The requests the call makes, in order, each with the response to give                                                                                               |
| `expect`         | One outcome per step: an `answer`, a `dataset`, an `operation` or an `error`                                                                                        |

A step that acts on an earlier result follows one: `choose` and a `fetch` with no target follow `search` or `choose`, and `result` follows `mint`. A `spec` in a step holds `spec_id`, the server's unmodified `definition`, and the `sentence` when the client knows it.

An exchange's request names its target in one of three ways. `path` is a path the client composes under the base URL, such as `/search/resolve`. `origin_path` is a root-relative URL the server returned, such as an artifact's `url`, which resolves against the base URL's origin and never against its path. `url` is an absolute URL outside the API, such as the signed storage URL a files route redirects to, which the client fetches with a bare request carrying no client header and no key. `json` holds the request body members that must match; a recorded scenario lists every member the client sent. The response gives `status`, the `headers` a client reads, and a `json` body or a `body_file`, a binary body in `scenarios/` whose name starts with the scenario's name.

An answer outcome gives `kind`, `status`, `sentence`, `complete`, the distinct `specs` of its definite parts, and its `gaps`, each with the `part` index, the gap `kind` and the number of `options`. A dataset outcome gives `rows`, `columns`, `complete`, its `members`, each with `parts`, `spec_id`, `content_version` and `sentence`, and the `missing` gaps. For one member the columns are the artifact's columns; a dataset of several members adds a `part` column. An error outcome gives the error `class` and the problem's `code`, or null when no problem document carries the error.

A recorded scenario's request and response bodies validate against their route's schemas in `served/openapi.json`, with one allowance: a required member whose schema admits null may be absent, because the server leaves null members out of the bodies it serves.

A client replays a scenario by serving the exchanges in order:

- A request matches the next exchange on its method and target, and on each listed `json` member, compared as parsed JSON values.
- A request with no exchange left, or one that matches none, fails the scenario, and so does an exchange the call never makes.
- `path` resolves against the base URL the replay gives the client, and `origin_path` against that base URL's origin, so a replay can run under any base URL form.
- Sleeps are not replayed: the replay injects the client's clock and sleep, and each wait the client asks for advances the clock at once.
- A recorded signed storage URL keeps its scheme, host and path, and its query reads `X-Goog-Signature=redacted`.

## DatasetRef

`dataset-ref.schema.json` is the frozen format a client writes for `data.ref` and reads back in any language: `format_version: 1` and one or more `members`, each `{parts, sentence, spec: {spec_id, definition}, content_version, artifact: {digest, rendering}}`. `parts` holds the variable text of each answer part the member answers, in query order, and is empty for a spec fetched outside an answer. `rendering` is the selector a client matches an artifact on, `{format, bundle}` with `layout: "frame"` for the frame layout, never the server's rendering object. Beyond the schema, each member's `definition.spec_id` equals its `spec.spec_id`, and members hold distinct `spec_id`s. `refs/` holds accepted examples and rejected ones, each `{format_version, name, description, ref}` with `expect: "accept"` or `error`, `{by: "schema"}` or `{by: "rule", reason}`, the reason `definition_spec_id_mismatch` or `repeated_spec_id`.

## Rules

`rules/README.md` states the rules every client implements the same way: the credentials path and file format, the key lookup order, the key format check, and the cache directory and layout. Each rule's vectors are in `rules/<rule>.json`.

## Validation

```bash
# Validate the contract against its schemas, the served document and the code registry,
# without importing this package
uv run ../../tools/validate_client_contract.py

# Replay the rule vectors through a reference implementation of each rule
uv run pytest tests/test_rules.py
```
