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
| `client-codes.json`         | The code, class and condition of each error a client raises itself                    |
| `client-codes.schema.json`  | The shape of `client-codes.json`                                                      |
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
| `client_codes()`  | `client-codes.json`, parsed                                 |
| `scenarios()`     | Every scenario record; raises `RuntimeError` when none load |
| `ref_examples()`  | Every `DatasetRef` example; raises `RuntimeError` when none |
| `rule(name)`      | The vector file for one of `RULES`                          |
| `contract_dir()`  | The installed contract directory                            |
| `*_schema()`      | Each schema, parsed                                         |

## Served document

`served/openapi.json` is `app.openapi()` of the composed dfo-apis app as deployed, with the Search, Datasets, Graph and Presentation surfaces mounted. `served/SOURCE.json` names the commit it came from as `{format_version, repository, commit, captured_from}`. It names the closed runtime models, `Manifest`, `ArtifactEntryModel`, every Search part model and `ProblemDetails` among them, which the per-surface schema routes leave open. Both clients generate wire types from it and parse tolerantly regardless of its `additionalProperties: false`: an unknown member or enum value passes through.

## Strict fields

`strict-fields.json` lists the members a client cannot act without. Each pointer names one property of one component schema, `/components/schemas/<Schema>/properties/<member>`. A `required` entry stays in its schema's `required` list and admits no null, so a client raises `ContractError` when one is missing or null. An `enums` entry keeps every listed value, the values a client branches on: the answer kinds, the part statuses and the operation statuses. A client still accepts a value no entry lists.

The answer's `kind` and each part's `status` are the discriminators of their unions in the served document. A search response with no `kind` raises `ContractError`, as a part with no `status` does, although the served document gives `kind` a default and leaves it out of `required`. A part `status` a client does not know raises nothing and makes the part an `unknown` gap. An answer `kind` a client does not know raises `ContractError`, because the client cannot read the answer.

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

A code that maps to `no_data_reason` also carries `gap_kind`, the kind of gap a no_data part with that reason becomes: `choice`, `no_data`, `not_understood` or `unsupported`. `choose` goes with `choice` and only with it. `choose` and `ask_data_question` name a next step inside an answer, so a code that also reaches a problem document takes another action. The website proxy's own `{error, retry, detail}` body carries its code in `error`. `capability_unavailable` is a well-formed request for a format, reshape or conversion class the deployment does not enable, answered 501; its action is `fix_request`, since the caller asks for another one, and `fetch-spec-capability-unavailable` pins it.

A client maps a refusal whose code has no row, or that carries no code, on its HTTP status:

| Status        | Error class             |
| ------------- | ----------------------- |
| 400, 422      | `InvalidRequestError`   |
| 401           | `AuthenticationError`   |
| 403           | `PermissionDeniedError` |
| 404, 410      | `NotFoundError`         |
| 408           | `DfoTimeoutError`       |
| 429           | `RateLimitError`        |
| any other 4xx | `InvalidRequestError`   |
| any other 5xx | `ServerError`           |

A 408 is a timeout the caller retries, and a 410 names a resource that is gone, so the caller stops as it does on a 404. A no_data reason with no row becomes a `no_data` gap.

An answer's gaps follow from its parts. A candidates part is a `choice` gap, its options the candidates. A no_data part is a gap of its reason's `gap_kind`, its options the no_data `options`, and of kind `timed_out` instead when the answer's `diagnostics.timed_out` is true. An unsupported part is an `unsupported` gap. A part whose status a client does not know is an `unknown` gap.

## Client codes

`client-codes.json` lists, sorted by code, each code a client sets on an error it raises when no problem document carries one: the `code`, the error `class` it raises, and a one-line `condition`. Both clients set an error's `code` from this list, so every error a client raises carries a code. A refusal keeps the server's code: the problem document's `code`, or the website proxy's `error`. A refusal whose body carries neither raises `refusal_without_code` with the class its HTTP status maps to, which its row gives as `by_status`; no other row does. No client code is a code in `actions.json` or `registries/codes.json`, and each class is one the scenario schema names. The list holds no warning, since a warning carries the server's code. A `SearchWarning` carries the code of the one way the readings differ from the question, a departure's or a warning's code or the verb of an operation Search did not run, and a null code when they differ in several ways. A `DataWarning` carries the manifest warning's code.

## Scenarios

A scenario is one client-level call, the HTTP exchanges it makes, and the outcome each step reaches. Each record is one file, `scenarios/<name>.json`, validated by `scenario.schema.json`.

| Member           | Holds                                                                                                                                                                                                                       |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `format_version` | `1`                                                                                                                                                                                                                         |
| `name`           | The kebab-case name, equal to the file name's stem                                                                                                                                                                          |
| `description`    | One or two sentences on what the scenario proves                                                                                                                                                                            |
| `origin`         | `recorded`, with the `base_url`, the deployed `dfo_commit` and `recorded_at`; or `synthetic`, with the recorded scenario it is `derived_from` and a one-line `edit`                                                         |
| `call`           | The steps, in order: `search`; `choose`; `fetch` of the previous answer, of a `spec` with `window`, `entities` and `facets`, or of a `ref`; `mint`; `operation`, which resumes a long build by its `operation_id`; `result`; `newest_version` of a `spec` |
| `exchanges`      | The requests the call makes, in order, each with the response to give                                                                                                                                                       |
| `expect`         | One outcome per step: an `answer`, a `dataset`, an `operation`, a `newest_version` or an `error`                                                                                                                                                |

A step that acts on an earlier result follows one: `choose` follows `search` or `choose`, a `fetch` with no target follows `search`, `choose` or another `fetch` with no target and acts on the last answer, and `result` follows `mint` or `operation`. A `fetch` of an answer or a spec, `mint` and `operation` take a `layout`, `long` by default or `frame`, and a `fetch` of an answer or a spec takes `strict`. A `spec` in a step holds `spec_id`, the server's unmodified `definition`, the `sentence` when the client knows it, and the server's `resolved.entity_groups` as `entity_groups` when it names any.

An exchange's request names its target in one of three ways. `path` is a path the client composes under the base URL, such as `/search/resolve`. `origin_path` is a root-relative URL the server returned, such as an artifact's `url`, which resolves against the base URL's origin and never against its path. `url` is an absolute URL outside the API, such as the signed storage URL a files route redirects to, which the client fetches with a bare request carrying no client header and no key. `json` holds the request body members that must match; a recorded scenario lists every member the client sent. The response gives `status`, the `headers` a client reads, and a `json` body or a `body_file`, a binary body in `scenarios/`. A scenario's own body files are named `<name>.<ext>` and `<name>-<n>.<ext>`; a synthetic scenario may instead name a body file its parent names.

A `newest_version` outcome gives the `content_version` the step found, or null. An answer outcome gives `kind`, `status`, `sentence`, `complete`, the distinct `specs` of its definite parts, and its `gaps`, each with the `part` index, the gap `kind` and the number of `options`. A dataset outcome gives `rows`, `columns`, `complete`, its `members`, each with `parts`, `spec_id`, `content_version` and `sentence`, and the `missing` gaps. For one member the columns are the artifact's columns; a dataset of several members adds a `part` column and gives `part_rows`, one entry per member in member order with the `part` value of that member's rows and their number. A fetch of an answer also gives `search_warning`, whether the fetch issued a `SearchWarning`. An error outcome gives the error `class` and its `code`: a refusal's code, a warning's code, which for a `SearchWarning` may be null, or for any other error the client raises itself its code in `client-codes.json`. A `VersionUnavailableError` also gives `newest_version`, the version the lookup found, or null. A dataset or error outcome of a step that made a long build gives `long_builds`, one entry per build the server ran on, in order: the `operation_id` the client polled last, the `poll_waits` in seconds before each poll, and the number of `resubmissions`.

Each outcome follows from the exchanges and `actions.json`, and `tools/validate_client_contract.py` fails a record whose `expect` says otherwise. It runs the steps over the exchanges by the rules below: a refusal followed by the same request is a retry where the retry rules allow one, except on a poll, where the long-build rules count retries, a refusal's class is its code's action or else its status, the class of an error the client raises itself is its code's row in `client-codes.json`, a gap's kind is its reason's `gap_kind`, option and candidate counts are those the bodies hold, a dataset's members, `complete`, `missing` and `search_warning` follow from the answer and the manifests, each POST carries its body, each build posts the body the rules give for the spec it builds, so the builds' order and spec_ids are checked, each Search request posts the input the rules give: the question, a choice's input, or a member's identity, each poll names the operation the client holds, and each signed download is the URL the files route's `location` names. A dataset's `rows`, `columns` and `part_rows` row counts are the Parquet bodies' and are not derived, except that `part` comes first and `part_rows` is given exactly when the dataset has several members, each entry's `part` is its member's value and their rows sum to `rows`.

A recorded scenario's request and response bodies validate against their route's schemas in `served/openapi.json`. A required member stays required when its schema admits null: the body carries it, as null or a value. A member the schema leaves optional may be absent or null, and a client reads both as null.

A client replays a scenario by serving the exchanges in order:

- A request matches the next exchange on its method and target, and on each listed `json` member, compared as parsed JSON values.
- A request with no exchange left, or one that matches none, fails the scenario, and so does an exchange the call never makes.
- `path` resolves against the base URL the replay gives the client, and `origin_path` against that base URL's origin, so a replay can run under any base URL form.
- Sleeps are not replayed: the replay injects the client's clock and sleep, and each wait the client asks for advances the clock at once. The waits before polls are the outcome's `poll_waits`.
- Every absolute URL a record holds, a `url` request or a `location` header, keeps its scheme, host and path, and its query reads `X-Goog-Signature=redacted`, whatever signed it.

The records also fix these client rules:

- A fetch builds each distinct `spec_id` once, in part order, and finishes one member (build, the sentence when the client lacks it, files, download) before the next.
- A fetch of a spec posts its narrowing as the build's `input.scope`, and posts no `scope` when it narrows nothing. A mint takes a layout and no narrowing, so it posts no `scope`. `window` posts `{"kind": "range", "start_year", "end_year"}` for a pair of years, or `{"kind": "all"}` or `{"kind": "latest"}`, and `facets` posts each axis as `{"ref": ...}`. The server's `scope.entities` replaces the spec's whole entity binding, so `entities` posts a whole map: the roles the spec's `definition.identity.entities` binds, with each role the caller names replaced by its refs and each role the caller binds to null dropped. Naming `origin` alone on a spec bound to `origin` and `destination` keeps `destination`. A fetch that names no role posts no `entities`.
- A built `spec_id` that differs from the posted one, or a spec with no sentence, takes its sentence from Search: the client posts the built definition's `identity` as one `dataset_resubmit` identity entry, with the member's first part as `variable_text`, or `"dataset"` when no answer part names it, and checks the answer's `spec_id` equals the built one. The entry's `entity_groups` holds the spec's `resolved.entity_groups` for each role the built identity binds to the same entities as the spec's definition, so a window or facets narrowing keeps every group and a role the narrowing rebound or dropped loses its group. The entry leaves `entity_groups` out when it keeps no group, as for a resumed operation, whose spec the client does not hold. The server reads a role posted without its group as its members, so a narrowed spec of flows to the least developed countries would read "44 recipients" in place of "the 44 Least Developed Countries".
- A dataset of several members puts the `part` column first, then each member's columns in member order, each column once. The `part` value on a member's rows is the member's `parts` joined by `"; "`, so a member that answers two folded parts reads `"health ODA disbursements; education ODA disbursements"`, or the member's sentence when `parts` is empty.
- A choice names its part by index, by variable text, or by a word that one gap's options mention, and its option by index or label; a candidate is named by index. A label is matched among the options that mention the word the part was named by. With no part named, exactly one gap must await a choice: a `choice` gap, or any gap with options.
- A choice raises `InvalidRequestError` and makes no request when no gap, or more than one, awaits it, when the named part is unknown, names more than one part or already has a reading, when the gap offers no options, or when the option is out of range or matches no label or several. It raises the same when an entry it would post cannot be built: a part with no `variable_text`, or a part of a status the client does not know that has no `restatement`. A chosen option whose `data_query` holds other than one part raises `ContractError`.
- Every pick of a candidate asks Search for the picked answer, and no client builds it locally. In a one-part answer the pick posts `dataset_resubmit`, without `raw_query`, with one identity entry: the candidate's identity and `entity_groups`, and the part's `variable_text`, `separate` and `operations` as below. The picked answer, its sentence with any "Assumed: …" clause, and its assumptions are Search's response. A choice of a no_data option in a one-part answer posts the option's `data_query` as the input.
- In a several-part answer a choice posts `dataset_resubmit`, without `raw_query`, with one entry per part in part order. The chosen candidate posts an identity entry with its identity and `entity_groups`, and the chosen option its `data_query`'s one part as a parsed entry. A definite part posts an identity entry with its spec's identity and `entity_groups`. A candidates part left alone posts a `candidates` entry: its candidate set in the server's order, each with its identity and `entity_groups`, and its `ordering` and `truncated` echoed. The server cannot recompute that order, because it rests on the first answer's bind election and availability reads, and a parsed entry would cost an LLM bind. Any other part posts its `restatement` as a parsed entry, or a placeholder entry with its no_data `reason` or unsupported `code` when it has none. Every entry but a parsed one carries the part's `variable_text`, `separate` when true and `operations` when the part has any; `entity_groups` is left out when the spec has none.
- Each identity a choice posts echoes its reading's assumptions as `assumptions`: the chosen candidate's entry carries its part's `reading_assumptions` under the candidate's `spec_id`, a definite part's entry carries the part's `assumptions`, and each candidate of a `candidates` entry carries its own `reading_assumptions` entry. Search then states the echoed items that still hold, so the picked answer states what that reading assumed. `[]` states none. An identity with no `assumptions` reads as a fresh request, so a client that holds no list for a reading, from a part that carries no `assumptions` or `reading_assumptions`, posts none. The identity entry that asks for a built sentence posts no `assumptions`, since it reads only the spec's own `rendered_sentence`.
- A strict fetch raises the manifest's first warning above `info` severity as a `DataWarning` with that warning's code, before the sentence lookup and the download.
- An identity resubmit whose answer is not one definite part of the built `spec_id`, with a sentence, raises `ContractError`.
- The `frame` layout posts `{"format": "parquet", "bundle": false, "layout": "frame"}` as the build's `options` and downloads the artifact whose rendering's `layout` is `frame`; the `long` layout posts `{"format": "parquet", "bundle": false}` and downloads the bare Parquet artifact with any other layout. A fetch of an answer with several members in the `frame` layout raises `InvalidRequestError` before any build.
- A download asks the route that redirects to the signed URL: an artifact's `url`, or the file selector. A refused signed URL, or bytes that do not hash to the artifact's digest, ask the route once more, and a second failure raises `ServerError` with `download_failed` or `digest_mismatch`.
- A declined or catalogue answer is not complete, and fetching it raises `DeclinedError`.
- Fetching an answer with a gap raises `IncompleteAnswerError` before any request, unless the fetch is partial and some part has a reading.
- A fetch of an answer issues one `SearchWarning` when a definite part has an operation Search did not run, a departure, or a warning at severity `warn` or `degraded` with a `message`, or the answer's `diagnostics.warnings` holds such a warning. An `info` warning, and a warning with no `message`, issue nothing. Each way is one line a client shows: `Not done: <verb> (<phrase as JSON>)` coded by the verb, the departure's `text` coded by its code, or `Note: <message>` coded by the warning's code, in part order with the diagnostics warnings last, and ways that show the same line count once, under the first one's code. A strict fetch raises the `SearchWarning` before any build, with the code of its one line, or a null code when there are several lines. A strict fetch raises it before the `frame` layout's several-member check.
- A ref's version is found in this order: the snapshot route and the files route at the stored digest; the file selector, `GET /datasets/{id}/{content_version}/file` with the ref's rendering as the query; and a build of the ref's spec in the ref's rendering, which must answer the same `content_version`. A snapshot refusal other than `artifact_expired` ends the lookup, so `snapshot_not_found` raises `NotFoundError` with no further request. A files route or selector refusal of `artifact_expired`, `artifact_not_found` or `not_found` takes the next step, and a selector file of another digest is not the stored file. A rebuild that answers another version, or that raises `InvalidRequestError`, `NotFoundError` or `VersionUnavailableError`, ends in `VersionUnavailableError` with the first refusal's code, or `version_unavailable` when nothing refused. Its `newest_version` is the version the rebuild answered, or else the newest stored version, which `GET /datasets/{id}/versions?limit=1` lists alone, as a `newest_version` step finds it, since the route lists versions newest first and needs no key; or null when that route lists none or refuses with `NotFoundError`. Any other refusal of the versions route raises.
- A build answers 200 with the manifest, or 202 with an operation the client polls at `/datasets/operations/{operation_id}`, the id of the operation it holds. A mint's build answers 202; a record whose mint answers 200 fits no mint step. Before each poll it waits the `Retry-After` of a 429 that refused the last poll, else the last operation resource's `retry_after_ms`, else the `Retry-After` of the response that carried that resource, else a backoff of 1, 2, 4 and then 8 seconds that advances only when it is used.
- A 429 poll spends no retry. Any other retryable refused poll spends one of the two retries, which a poll that answers restores, and a refusal that is not retryable raises. A poll that answers a failed operation whose error is retryable posts the same build once more and polls the operation it starts; a second failure, an error that is not retryable, and a resumed operation, whose build request the client does not hold, raise the operation's error. A refused poll whose code is `operation_failed` is a failed operation with that error. A 202 or resumed operation that already reads `failed` raises its error before any poll.
- Any other refusal is retried when it is retryable: as a problem document's `retryable` says, or the website proxy's `retry` of `retry_after_delay`, else when its status is 408, 429, 500, 502, 503 or 504. A client makes two retries by default, and the scenarios assume that default; a build cut off by a retryable 504 is posted once more only, and a second 504 raises. A refusal that is not retryable, or one past the retries, raises its error.
- A retry waits `Retry-After` when the response carries it. The scenarios hold no outcome for the waits before a retry, so a replay does not pin them.

## Recording

`tools/capture_client_contract.py` refreshes the served document and the scenarios. Its `openapi` command calls `dfo_apis.app.create_app()` in process, with the deployed surface flags (Datasets, Graph and Presentation beside Search), in a dfo-apis checkout with no uncommitted changes and no untracked files, over the checkout's own test origin catalogue and test graph release published into a temporary directory, and writes `served/openapi.json` and `served/SOURCE.json`. It refuses an environment that `uv sync --frozen --check` with the five extras below finds out of step with the checkout's `uv.lock`, so the checkout needs its environment first. From the repository root, with the checkout at `../dfo-apis`:

```bash
uv sync --directory ../dfo-apis --extra eval --extra api --extra engine --extra datasets --extra graph
uv run tools/capture_client_contract.py openapi --dfo-apis ../dfo-apis
```

Its `record` command runs every scenario against a base URL, dev's Cloud Run URL by default, checks each live answer still has the shape its scenario names, derives the synthetic scenarios from the fresh records with no request, and then runs the validator. A recorded scenario holds only exchanges the server made, and the run refuses one that a canned exchange answered; the record names the commit the server's responses name. It fails when the validator reports a problem or the binary bodies pass 5 MB. `--only` records the scenarios it names. `--offline` makes no request: each recorded scenario's steps run again over its own exchanges and rewrite its `expect`, and each synthetic scenario is derived again from its parent, so a change to the outcome rules or to `client-codes.json` reaches the records without a recording:

```bash
uv run tools/capture_client_contract.py record --offline --only answer-declined choose-refused-no-choice
```

Each record names the commit in the responses' `x-dfo-commit` header, and the run refuses responses that name none or several. To record against a local app, run the composed app, with the same four surfaces mounted, from the checkout the document came from, with `DFO_BUILD_COMMIT` set to that checkout's commit, then pass its base URL:

```bash
uv run tools/capture_client_contract.py record --base-url http://127.0.0.1:8083/api
```

A record's `origin.base_url` is the base URL the run was given, a local app's included. A local app's signed URL, which its launcher serves at `/__e2e__/blob/{bucket}/{object}` on the API's origin, is written as `https://storage.googleapis.com/{bucket}/{object}` with its query redacted, as a deployment signs it, because a production download is cross-origin and the client sends no key there; the run refuses a record whose signed URL or `location` header still names the base URL's host. A server answers a version it has built inline, so `mint-async-build` and `operation-resume` each need a spec just above the 100,000-row sync budget that the server has not built: the DAC2a recipient flows from 2018 to 2024 for the mint, and from 2017 to 2024 for the resumed operation. A local recording therefore starts on a fresh datasets database with `DATASETS_SYNC_BUDGET_ROWS=100000`. `--scenarios-dir` writes records outside the package, unvalidated.

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
