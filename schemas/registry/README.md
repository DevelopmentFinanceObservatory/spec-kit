# schemas/registry

The code registry assigns one permanent, language-neutral meaning to every warning and refusal code, so that every consumer of a contract, the engine, the harness, a website, a partner integration, reads the same meaning for the same code.

The registry holds wire codes from Search, Presentation, Datasets, Graph, Skills, and MCP tool results, plus the platform-generic codes any contract can render. The platform's `GET /codes/{code}` lookup, mounted on every surface, serves only codes this registry carries. `schemas/registry/code-registry.schema.json` (draft 2020-12, `$id` `https://raw.githubusercontent.com/DevelopmentFinanceObservatory/spec-kit/main/schemas/registry/code-registry.schema.json`) validates `registries/codes.json`. The `$id` sits under the repo's raw-content path because the kit has no schema-hosting service. Should one exist later, the `$id` moves and the old one is retired like any other schema identifier.

`registries/codes.json` is canonical. `python/spec-kit/src/dfo_spec_kit/registry/codes.json` is a byte-identical package mirror because `uv_build` only includes files within a package directory. `dfo_spec_kit.registry` loads the mirror through `CodeRegistry`, `CodeEntry`, and `CodeMapping`. `uv run tools/validate_registry.py` fails if the files diverge.

## Format

The registry file is one JSON object:

```json
{
  "format_version": 1,
  "entries": [/* sorted by code */]
}
```

`format_version` is the registry format's own version. The schema pins it to the exact value it validates (`1` today). A version-2 registry does not validate against this schema at all. It needs `code-registry.schema.json`'s own next version, published alongside it. It has nothing to do with `introduced_in` or `schema_version`.

Each entry:

| Field             | Meaning                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------ |
| `code`            | The unique, lower-snake-case spelling: `^[a-z][a-z0-9]*(_[a-z0-9]+)*$`.                          |
| `classification`  | `refusal` or `warning`. See below.                                                               |
| `definition`      | The stable semantic definition: one to three sentences, present tense, no implementation detail. |
| `owning_contract` | The short name of the normative contract document that defines the code.                         |
| `owner`           | The team accountable for the code's meaning.                                                     |
| `introduced_in`   | The owning contract's version that first defined the code.                                       |
| `mappings`        | Every endpoint/door/channel combination the code is allowed to appear on (see below).            |
| `lifecycle`       | `active` or `retired`.                                                                           |

## Classification

`classification` collapses transport into two buckets:

- **`refusal`** covers a request that cannot be answered. It appears in an MCP tool result's refusal envelope, in a non-2xx RFC 9457 problem-details body, a 200 response's `no_data` reason, a 200 response's `unsupported` reason, a 200 `no_data` response's `flagged_match.gate`, the quality gate that failed and left a flagged nearest-real match in place of a confident answer, or a returned Spec's `observations.omitted.reason`, the 200 response's own explanation for why that Spec's observations preview carries no series.
- **`warning`** covers a diagnostic attached to an otherwise-answered request. It appears in `diagnostics.warnings`, an SSE stream's terminal `done` event, a Datasets manifest's `warnings` array, a Datasets options envelope's `warnings` array, or a Skills API list or skill body's `warnings` array.

A code can appear on more than one surface. For example, `facet_not_available` is a 422 problem-details refusal through a structured door and a `no_data` reason for a text query. It has one registry entry with multiple mappings. A mapping records every permitted surface for that meaning, with two exceptions, both set out under `mcp.tools` below: a platform code presentation-mcp forwards, and the website proxy's surface. `(endpoint, channel)` is unique within an entry's `mappings`. A code that varies by door on one endpoint and channel uses one mapping with every permitted door.

`observations_omission_reason` is `tools/validate_registry.py`'s one channel legal under both classifications, because three `observations.omitted.reason` codes are warning-classified: `observations_preview_too_large`, `observations_windowed_scope_unsupported` and `observations_converted_scope_unsupported`. The first is also minted as a `diagnostics_warning`/`done_event_warning`. The other two also ride `diagnostics_warning`. Every other `observations.omitted.reason` code is `refusal`-classified and reaches this channel alone, never `diagnostics.warnings`, since nothing else on this list is ever minted as a Warning.

## The addition rule

Adding a code is compatible. A published code keeps its meaning, a retired spelling stays reserved, and a new meaning gets a new code, which is why `lifecycle` only moves from `active` to `retired` and why the schema rejects a repeated `code` value.

Once a release tag exists, `tools/validate_registry.py` diffs the working registry against the tagged one and fails on any change to a published entry's fields other than `lifecycle` and `mappings`. Before the first release tag it reports that it skipped this comparison.

Published mappings may grow. A mapping may gain doors, and an entry may gain a mapping whose channel matches its classification. The validator matches mappings by `(endpoint, channel)`, doors only widen, and `http_status` stays fixed, so an active code's mappings can overstate where it appears until the code is retired.

Retiring a door is a schema change, reviewed as one: dropping a value from `code-registry.schema.json`'s own `doors` enum releases every published mapping that named it. `tools/validate_registry.py`'s `schema_legal_doors` reads the schema's current door enum and discounts a retired door from the tagged side only of the growth and mapping-removed comparisons above, so a mapping may drop a door the schema no longer defines, and a mapping whose only published door was retired this way may disappear entirely, without either reading as the client-facing narrowing the rule otherwise forbids.

## Endpoint / door / channel vocabulary

`mappings` entries use a closed vocabulary from each contract's Endpoints section and the platform's routing and default-classification surfaces. The schema enforces it.

- **`endpoint`** lists the Search endpoints `search.resolve` (`POST /api/search/resolve`) and `search.options` (`POST /api/search/options`).

  The Datasets endpoints are `datasets.register` (`POST /api/datasets/register`), `datasets.mint` (`POST /api/datasets`), `datasets.definition` (`GET /api/datasets/{id}`), `datasets.options` (`GET /api/datasets/{id}/options`, the write-free preview door), `datasets.members` (`POST /api/datasets/{id}/members`, the write-free member read), `datasets.snapshot` (`GET /api/datasets/{id}/{content_version}`, and `GET /api/datasets/{id}/versions`, which pages a dataset's servable versions), `datasets.files` (`GET /api/datasets/{id}/{content_version}/files/{digest}`, which answers a stored artifact with a 302 redirect to its download link, and `GET /api/datasets/{id}/{content_version}/file`, which selects a stored version's file by format, bundle and layout, renders it when it is missing, and answers the same redirect), `datasets.description` (`GET /api/datasets/{id}/{content_version}/description`, a stored version's description, for a `spec_` id with a `cv_` version or an `ops_` id with an `ocv_` version, and `GET .../description/values`, which pages one dimension's values), `datasets.observations` (`GET /api/datasets/{id}/{content_version}/observations`, a stored version's observations), `datasets.pin` (`POST /api/datasets/{id}/{content_version}/pin`), `datasets.publish` (`POST /api/datasets/frames/publish`, the door an analysis service uses to publish an already-computed frame under its own identity pair), `datasets.recipe` (`PUT` and `GET /api/datasets/frames/{spec_id}/{content_version}/recipe`, the recorded computation behind a published frame, and `GET /api/datasets/recipes/replays/{replay_key}`, the frame stored under a replay key), `datasets.refresh` (`POST /api/datasets/frames/refresh`, which rebuilds a dataset frame's rendering on current data), `datasets.operations` (`GET /api/datasets/operations/{operation_id}`), `datasets.analysis` (`POST /api/datasets/analysis`, which runs an analysis recipe tree and answers its derived frame, and `POST /api/datasets/analysis/anchor`, which resolves a recipe's latest period and stores nothing), `datasets.exports` (`POST /api/datasets/exports`, which prepares an export file of a stored frame and answers its facts), and `datasets.export_link` (`GET /api/datasets/exports/{export_id}`, which answers a 302 redirect to the export file's download link).

  The Presentation endpoints are `presentation.write` (`POST /api/presentation/components`, `PATCH /api/presentation/components/{component_id}`), `presentation.component_read` (`GET /api/presentation/components/{component_id}`), `presentation.component_data` (`GET /api/presentation/components/{component_id}/revisions/{revision}/data`, the rows behind one revision), `presentation.render` (`POST /api/presentation/components/{component_id}/revisions/{revision}/render` and `GET .../render/data`, which draw a stored revision on a named target frame and mint no revision), `presentation.frames` (`GET /api/presentation/frames/{spec_id}/{content_version}` and `GET /api/presentation/frames/{spec_id}`), and `presentation.shares` (every route under `/api/presentation/shares`). Search's and Datasets' `schema`, `capabilities`, and `healthz` routes and Presentation's `capabilities`, `healthz`, and `release` routes carry no permanent codes.

  The Graph endpoints are `graph.node` (`GET /api/graph/node/{dcid}`), `graph.nodes` (`POST /api/graph/nodes`, a batch of node reads), `graph.members` (`GET /api/graph/node/{dcid}/members`, one page of a node's list), `graph.roots` (`GET /api/graph/roots`), `graph.documentation` (`GET /api/graph/documentation/{dcid}`, a source's document), `graph.sources` (`GET /api/graph/sources`), and `graph.resolve` (`POST /api/graph/resolve`). `graph.node` and `graph.documentation` carry `node_not_held`, `request_invalid`, `service_unavailable`, and `upstream_error`; `graph.nodes` and `graph.resolve` carry `request_invalid`, `unsupported_schema_version`, `service_unavailable`, and `upstream_error`; `graph.members` carries `node_not_held`, `request_invalid`, `service_unavailable`, and `upstream_error`, the last when the read that checks whether the graph holds the node fails upstream; and `graph.roots` carries `service_unavailable`. A node the graph does not hold answers inside a `graph.nodes` batch as a `not_held` entry, so that endpoint carries no `node_not_held`. Graph's `sources` and `capabilities` routes carry no permanent codes.

  The Skills endpoints are `skills.list` (`GET /api/skills`), `skills.fetch` (`GET /api/skills/{skill_id}`), and `skills.resource` (`GET /api/skills/{skill_id}/resources/{path}`). All three carry `service_unavailable`, which is how they report an index that has not finished its first build. `skills.fetch` and `skills.resource` carry `skill_not_found` and `request_invalid`, and `skills.resource` alone carries `skill_resource_not_found`. Every skills warning code appears on `skills.list`, and each one describing a single skill also appears on `skills.fetch`. Skills' `capabilities` route carries no permanent codes.

  The MCP endpoint is `mcp.tools`, which holds every code an MCP server writes in a tool result: the values of the dfo-ai data-mcp and analysis-mcp `ToolErrorCode` enums, the refusals presentation-mcp writes, and the refusals the harness combined server writes, among them `tool_not_found` and `rate_limited`. `mcp.tools` and the `mcp_refusal` channel go only with each other, which the schema enforces, so these mappings carry no `http_status`. A code an HTTP route also serves keeps its HTTP mappings beside its `mcp.tools` mapping, as `request_invalid` and `internal_error` do, and no HTTP route returns a code that maps here alone. presentation-mcp also forwards platform codes it does not write itself, such as `edit_conflict`, in the same envelope, and those keep their `presentation.*` mappings with no `mcp.tools` mapping.

  A website proxy code may share its spelling with an entry that maps to `mcp.tools` alone, as `rate_limited` and `transient_unavailable` do. The entry's meaning holds on both surfaces, and `python/client-contract/src/dfo_client_contract/proxy-codes.json` holds the proxy surface and its statuses.

  The platform endpoints are `platform.any`, for requests no mounted surface classifies, and `platform.codes` (`GET /codes/{code}`). `platform.any` covers unmatched routes, disallowed methods, stalled deadlines, and uncaught exceptions. A code may map to a platform endpoint and a contract endpoint.

- **`doors`** names the ways a request reaches the engine. Omit it when a code applies to every door. The values are `raw_text`, `parsed`, `dataset_resubmit`, and `options`. `raw_text` and `parsed` are Search's text doors. `dataset_resubmit` re-resolves parts of a prior response: its identity and candidates entries share the same promote machinery a resolved identity already carries (no extraction, no re-binding), and its `parsed` entries share the parsed door's own per-part fan-out (still no extraction, but binding does run). A code raised only inside extraction — the one phase every `dataset_resubmit` entry kind skips — carries only the text doors, so conjunction handling, near-neighbour checks, purpose broadening, and constraint-filter fallback are scoped there. A code raised inside binding, materialisation, the promote machinery, or the shared observations fetch carries `dataset_resubmit` alongside the text doors. `options` names the single door of its own endpoint.

  `doors` and `endpoint` are coupled. The schema enforces the relationship. `search.resolve` accepts only `raw_text`, `parsed`, and `dataset_resubmit`. A `search.options` mapping with `doors` must use exactly `["options"]`. A code on two endpoints needs one mapping for each endpoint.

  Datasets, Presentation, Graph, Skills, MCP, and platform mappings omit `doors`. Each endpoint value names one route or platform-wide concept.

- **`channel`** identifies where the code appears.

  - `problem_details` is a non-2xx RFC 9457 body.
  - `no_data_reason` is the 200 `no_data` status's `reason`.
  - `unsupported_code` is the 200 `unsupported` status's `code`.
  - `diagnostics_warning` is an entry in the response envelope's `diagnostics.warnings`.
  - `done_event_warning` is an entry in the SSE stream's terminal `done` event, for trailing work, option-coverage and observation-preview enrichment, that finishes after the `response` event has shipped.
  - `manifest_warning` is an entry in a Datasets manifest's `warnings` array or a Datasets options envelope's `warnings` array.
  - `flagged_match_gate` is the 200 `no_data` status's `gate`, the failed quality-gate code that produced a flagged nearest-real match in place of a confident answer.
  - `skills_warning` is an entry in a Skills API list body's or skill body's `warnings` array.
  - `observations_omission_reason` is a returned Spec's `observations.omitted.reason`, the 200 response's own explanation for why that Spec's observations preview carries no series. The Datasets observations body carries the same `omitted` object.
  - `mcp_refusal` is the refusal envelope in an MCP tool result's structured content, made of `code`, `retry_mode`, `message` and `fix`. `retry_after_s` names the wait when the refusal has one. `upstream_code` names the dfo-apis code behind the refusal, on data-mcp and analysis-mcp, and `capability_version` the capability version the upstream answered with, on data-mcp alone. data-mcp sends those three as null when it has no value, and the other servers leave them out. The envelope has no HTTP status.

  `http_status` is present only when `channel` is `problem_details`.

## Severity and human-facing messages

The registry has no severity or message template. Each occurrence supplies them through the contract's `Warning` shape (`{code, severity, message}`), problem-details body (`{code, title, detail, ...}`), or the `message` in an `mcp_refusal` envelope, which carries no severity. The registry defines a code's meaning and permitted locations.

## Adding an entry

1. Confirm the code's meaning is defined in a normative contract document. The registry makes decided semantics executable. It does not decide them.
1. Pick a lower-snake-case spelling not already used by an active or retired entry.
1. Write the entry with every required field, add it to `registries/codes.json` in code order, and run `uv run tools/validate_registry.py`.
1. If the same semantic already has an entry on a different transport, add a mapping to that entry.

## Unit registry

`unit-registry.schema.json` (draft 2020-12) validates `registries/units.json`, which `dfo_spec_kit.units` reads. Each row is closed to the members of its kind, and `format_version` is pinned to `1`. `registries/README.md` states the rules the file declares, and `uv run tools/validate_unit_registry.py` checks the schema, the sort order, uniqueness, the prefix rule and the package mirror.
