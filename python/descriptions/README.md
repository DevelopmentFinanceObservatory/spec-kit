# dfo-spec-kit-descriptions (Python)

This package holds the contract for step records and descriptions. A step record says what was done to a result, one step per operation. A description answers a reader's five questions about a dataset version, a computed version or a card revision. The schemas, the operation list and the rules below are normative. The packaged vector suite pins them, and where this README and the vectors disagree, the vectors win and the disagreement is a bug. See the [repo README](../../README.md) for how this package fits with the rest of `dfo-spec-kit`.

The import name is `dfo_descriptions`. It depends on the standard library and `rfc8785`, the JCS serialiser the size bound counts bytes with.

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
uv run ty check src/dfo_descriptions
```

## Contents

`src/dfo_descriptions/schemas/` ships with the package.

| File                      | `$id` under `https://developmentfinance.org/descriptions/schema/v1/` | Holds                                                                                   |
| ------------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `steps.schema.json`       | `steps.schema.json`                                                  | A step list, and the shared `ref`, `version_ref`, `unit` and `step_members` definitions |
| `description.schema.json` | `description.schema.json`                                            | A description. Its steps reference `steps.schema.json` by relative `$ref`               |
| `operations.json`         |                                                                      | The operations a step can name                                                          |
| `operations.schema.json`  | `operations.schema.json`                                             | The shape of `operations.json`                                                          |

A validator resolves `description.schema.json` against `steps.schema.json` by registering both under their `$id`.

```python
from dfo_descriptions import StepsError, check_steps, description_schema, steps_schema

# after the step list validates against steps_schema(); a description that
# validates against description_schema() goes to check_description instead
try:
    check_steps(steps)
except StepsError as error:
    print(error.reason, error.step, error.detail)
```

| Function                         | Returns                                         |
| -------------------------------- | ----------------------------------------------- |
| `steps_schema()`                 | `steps.schema.json`, parsed                     |
| `description_schema()`           | `description.schema.json`, parsed               |
| `operations()`                   | `operations.json`, parsed                       |
| `operations_schema()`            | `operations.schema.json`, parsed                |
| `schema_dir()`                   | The installed `schemas/` directory              |
| `check_steps(steps)`             | `None`, or raises `StepsError` on a broken rule |
| `check_description(description)` | `None`, or raises `StepsError` on a broken rule |
| `steps_bytes(steps)`             | The bytes the size bound counts                 |

## Step records

A step list runs in order. Each step has an `id` unique in its list and an `op`. A step that takes one input names it in `input`, and a step that takes several names them in `inputs` by role, such as `numerator` and `denominator` or `left` and `right`. An input is an earlier step's id or a version reference `{id, content_version, label}`, where `id` is `spec_` with a `cv_` content version or `ops_` with an `ocv_` one. A choice the DFO made while minting a version takes no input.

| Member                   | Holds                                                                                                                |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `policy`                 | The operation's one fixed policy for aligning periods and handling gaps                                              |
| `params`                 | What the caller chose, such as a filter's predicate, a rank's limit or the entity a share takes                      |
| `options`                | A retrieval's mint options                                                                                           |
| `kept`                   | Counts of what the step kept, by name                                                                                |
| `left_out`               | Data gaps, each `{reason, count, of}`, with `of` one of `entities`, `periods`, `entity_periods` or `pooled_entities` |
| `period_coverage`        | How many entities each period's total covers, for share, quotient and aggregate                                      |
| `entity_coverage`        | How many of the window's periods each ranked entity holds, for rank                                                  |
| `choice`                 | A choice of source at mint: the source chosen, the others, and the ranking applied in order                          |
| `summed`                 | A sum at mint: how many variables were summed, and up to 64 of them                                                  |
| `converted_from`, `unit` | The unit a conversion started from, and the unit every step produces                                                 |
| `sides`                  | The entities each side of a two-sided operation holds, by input role; compare records it                             |

A step is a fact stored once, so every label inside it is optional: a reference, a unit or a version reference needs only its `ref` or `id`. When a description is served, a reference inside a step carries the vocabulary's label, else the label stored with the step, else none and a gap. A producer with no vocabulary sends refs alone.

A unit names its dcid as `ref`, its ISO 4217 code as `currency`, or both. A converted unit is `{currency, prices, base_year}`, the frame grammar's own shape, so `ref` never holds a currency code. `prices` is `current` or `constant`, and a unit names `base_year` exactly when its prices are constant, as the frame grammar and the identity core require. Either side of a composed unit `{numerator, denominator}` may be a unit or a variable `{stat_var, label?}`, such as the population of a per-capita rate. Every dcid is bare, without a `dcid:` prefix, and a variable side refuses one.

`left_out` holds data gaps only. The caller's own selection, which the analysis engine records as `entity_not_selected`, `unit_not_selected`, `outside_window`, `predicate` or `beyond_limit`, is a parameter and stays out of it.

A `pooled_entities` count names its graph role in `role`, such as `financingProvider`, and counts the distinct parties of that pooled role across all the step's inputs. A `party_total` gap takes one entry per pooled role. Those parties are never among the entities the step kept, so a reader words them with the role's noun and keeps them out of the kept entities' total. `role` is required with `pooled_entities` and refused with any other `of`.

`sides` is an object keyed by the step's input roles, `left` and `right` for compare. Each value maps a role column, such as `financingProvider` or `financingRecipient`, to the entity that side holds in it, written as a dcid in the form `params` use: `{"left": {"financingProvider": "dcid:country/FRA", "financingRecipient": "dcid:country/KEN"}, "right": {"financingProvider": "dcid:country/GBR", "financingRecipient": "dcid:country/KEN"}}`. A role column appears when the side's slice holds exactly one value in it; a column with several values or none is absent, so an empty map is valid. It lets a reader say which entity each side holds, including when the sides are two separate retrievals that bind no entity. `check_steps` requires `sides` on every compare step, keyed by exactly its input roles. The schema defines the member for any two-sided operation, and only compare records it.

`op`, `policy` and `left_out[].reason` are bounded lower-snake-case strings, so a step naming an operation or reason `operations.json` lacks still validates. A reader renders an unknown one as a gap that names it.

### Structure rules

`check_steps` checks what the schema cannot, on a list that already validates against `steps.schema.json`, and raises on the first rule broken. `check_description` runs the count bound, the I-JSON check and the id, input and `sides` rules on a description's `steps` when they are a list, then checks the description's caveats and gaps and its drawn dimensions' `values`.

| Reason                        | Meaning                                                                                                            |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `too_many_steps`              | The list holds more than 64 steps (`MAX_STEPS`)                                                                    |
| `steps_too_large`             | The stored list takes more than 65,536 bytes as JCS UTF-8 JSON (`MAX_STEPS_BYTES`)                                 |
| `not_i_json`                  | The list holds a lone surrogate, a non-finite number or an integer outside ±(2^53 − 1), which JCS cannot serialise |
| `duplicate_step_id`           | A step's id repeats an earlier step's                                                                              |
| `unknown_input`               | An input names a step id no step in the list holds                                                                 |
| `input_not_earlier`           | An input names the step itself or a later step                                                                     |
| `missing_sides`               | A compare step has no `sides`                                                                                      |
| `sides_roles_mismatch`        | A compare step's `sides` keys are not exactly its input roles                                                      |
| `unknown_caveat_step`         | A description's caveat names a step its `steps` do not hold                                                        |
| `unknown_gap_step`            | A description's gap names a step its `steps` do not hold                                                           |
| `drawn_values_count_mismatch` | A drawn dimension lists `values` whose number differs from its `count`                                             |
| `duplicate_drawn_value`       | A drawn dimension's `values` repeat a `ref`                                                                        |

The size bound is on the stored list and `check_steps` checks it where the list is written, counting the list serialised by JCS (RFC 8785). `check_description` leaves it out, since a served step's labels come from the vocabulary and its `text` is added, so the served bytes differ from the stored list's. JCS spells numbers as ECMAScript does, so a TypeScript consumer counts the same bytes with `JSON.stringify` on the same members, once it refuses what I-JSON cannot carry (`String.prototype.isWellFormed`, `Number.isSafeInteger`, `Number.isFinite`). The bounds are checked first, on the whole list. Their errors, and those for a caveat or a gap, have no `step`. An input may name only an earlier step, so a list that passes cannot hold a cycle. Reasons are permanent once published. A changed meaning gets a new reason.

## Operations

`operations.json` lists, for each operation, its kind, whether it takes one input, inputs by role or none, its one fixed policy, the rule for the unit it produces, the coverage it records, and the data gaps it can leave out with what each counts. The analysis operations carry the policy names and exclusion reasons the `ai` repo's analysis engine already uses.

| `op`              | Kind            | Inputs                     | Policy                              | Unit                                  | Coverage   | Known `left_out` reasons                                                               |
| ----------------- | --------------- | -------------------------- | ----------------------------------- | ------------------------------------- | ---------- | -------------------------------------------------------------------------------------- |
| `retrieve`        | retrieval       | one version                | `retrieve_minted_version`           | as recorded                           |            |                                                                                        |
| `sum`             | analysis        | `left`, `right`            | `sum_of_intersect_periods`          | the inputs' shared unit               |            | `period_unmatched`, `unit_unbound`                                                     |
| `quotient`        | analysis        | `numerator`, `denominator` | `quotient_of_intersect_periods`     | composed                              | per period | `unmatched_key`, `period_unmatched`, `zero_denominator`, `unit_unbound`, `party_total` |
| `share`           | analysis        | `numerator`, `denominator` | `share_of_intersect_periods`        | per cent                              | per period | `period_unmatched`, `unit_unbound`, `party_total`                                      |
| `growth`          | analysis        | one                        | `growth_endpoint_years`             | the input's, or a dimensionless ratio |            | `endpoint_missing`, `zero_or_sign_crossing_base`, `non_finite`, `unit_unbound`         |
| `rank`            | analysis        | one                        | `rank_what_is_present`              | the input's, or none for an ordinal   | per entity | `party_total`, `unit_unbound`                                                          |
| `aggregate`       | analysis        | one                        | `aggregate_skip_absent_grain_cells` | the input's                           | per period | `party_total`, `unit_unbound`                                                          |
| `filter`          | analysis        | one                        | `filter_row_value_predicates`       | the input's                           |            |                                                                                        |
| `compare`         | analysis        | `left`, `right`            | `compare_intersect_periods`         | the inputs' shared unit               |            | `period_unmatched`, `unit_unbound`                                                     |
| `join`            | analysis        | `left`, `right`            | `join_inner`                        | each side its own                     |            | `unmatched_key`, `unit_unbound`                                                        |
| `source_step`     | source pipeline | none                       | `source_pipeline_as_recorded`       | as recorded                           |            |                                                                                        |
| `choose_source`   | mint choice     | none                       | `facet_default_by_coverage`         | as recorded                           |            |                                                                                        |
| `sum_variables`   | mint choice     | none                       | `sum_family_members`                | the summed variables' shared unit     |            |                                                                                        |
| `convert`         | mint choice     | none                       | `convert_by_rate_and_deflator`      | converted                             |            | `currency_unmapped`, `deflator_missing`                                                |
| `continue_series` | mint choice     | none                       | `continue_with_source_estimates`    | as recorded                           |            |                                                                                        |
| `select`          | card selection  | one version                | `card_selection`                    | the version's                         |            |                                                                                        |
| `change`          | card derivation | one                        | `change_between_basis_periods`      | the input's, or per cent              |            |                                                                                        |

## Descriptions

A description carries `describes`, `title`, the five blocks `about`, `sources`, `freshness`, `steps` and `cite`, then `caveats`, `gaps` and `built_from`.

- `describes` is `{kind: "dataset_version", id, content_version}` with a `spec_` id, `{kind: "computed_version", …}` with an `ops_` id, or `{kind: "card_revision", component_id, revision}`.
- Each kind carries a fixed set of members, and a member it carries is present, `null` with a gap when unknown. A dataset version's `about` carries `measure`, `fixed`, `varies`, `variables`, `series`, `period` and `unit`; its `freshness` carries `prepared`, `latest_period`, `series_reaching_it`, `source_release`, `source_downloaded_at_mint`, `source_downloaded_since`, `next_release`, `estimated_values` and `provisional_values`; and its `cite` carries its `spec_` `id`, `content_version`, `retrieved` and `data`. A computed version's `about` carries `name`, `series`, `period` and `unit`; its `freshness` carries `computed`, `inputs`, `latest_period`, `series_reaching_it`, `estimated_values` and `provisional_values`; and its `cite` carries its `ops_` `id`, `content_version`, `retrieved`, `data` and `inputs`. A card revision's `cite` carries `component_id`, `revision`, then the version's `id`, `content_version`, `retrieved` and `data`. A card drawn from a version carries `about.drawn` and `about.unit`, and `freshness.latest_period`, `series_reaching_it`, `estimated_values` and `provisional_values`. A card drawn from a dataset version may also carry its version's `about.measure`, which names the version whatever subset the card draws, and `about.fixed`, the version's fixed values other than those the measure names. A card drawn from a computed version carries neither. A source card carries `about.name` and the three download and release members. A kind refuses the members of another kind's blocks.
- An id and its content version are of one family, `spec_` with `cv_` or `ops_` with `ocv_`, in `describes`, `cite`, `frame` and every input, including an input source's `ref`. Both schemas state the rule once, as `version_family` in `steps.schema.json`.
- No id, name, currency or date accepts a newline. Python's `re.search` lets a pattern's `$` match before a final newline, so each `$`-anchored pattern also refers to `one_line` in `steps.schema.json`.
- An open dimension with a known `count` carries `top`, up to two values by variable count, `more`, the count of the rest, and `values`, the route that pages them, provider included. A dimension whose values are unknown has `count: null` with a gap and none of the three.
- A source that is not an input carries `publisher`, `licence` and `series`, each `null` with a gap when unknown. A dataset version has no inputs, so its sources carry no `content_version`.
- `about.measure` carries a `definition`, `null` with gap `about.measure.definition` when nothing defines the measure. Each `about.fixed[].value`, on a version or a card, may carry a `definition`, the graph's description of the value, 1 to 4096 characters, absent when the graph holds none. A card's `about.drawn.dimensions[]` may list the `values` it draws on that dimension, each with its `definition`, in the order the card first draws them. The list holds 1 to 50 values, and its presence means it names every value the card draws there. A list longer or shorter than the dimension's `count`, or one that repeats a `ref`, breaks the structure rules, and a card that draws none or more than 50 leaves `values` out.
- Every reference is `{ref, label}`, and every unit, variable side and input version outside `steps` carries its `label` too. A label, source name, publisher or licence taken from the evidence stored at mint, in place of today's vocabulary, sits in an object carrying `at_mint: true`.
- A computed version's inputs, in `sources`, `freshness.inputs` and `cite.inputs`, name each version it was computed from by `id` and `content_version`, with a `label`. An input that no step retrieves, as on a version published without steps, has an unknown id: it is served with `id: null`, or in `sources` with `ref: null` and no `description` link, and a gap naming that member. Its label is the one stored with it, else its content version with a gap. Only an input, which carries `content_version`, may leave its `ref` null.
- `steps` is a step list whose every step adds `text`, the composer's sentence for that step, or `null` when the record holds none, with a gap. Both schemas build a step from `step_members` in `steps.schema.json`: a stored step closes it with `unevaluatedProperties: false`, so a stored list refuses `text`, and a served step requires `text` and closes it the same way.
- `caveats` are facts about the data in four closed kinds, `estimated`, `provisional`, `break_in_series` and `coverage_gap`. Each names at least one of the `series`, `entities` or `periods` it applies to, or the `step` that left data out, which must be a step in `steps`, and carries `text`, the composer's sentence for it. A `coverage_gap` that points at a step words what that step left out with the step's own party noun and reasons. The record stores the caveat's facts, and the composer writes `text` when it serves them.
- `gaps` lists the fields that could not be filled, as dotted paths. A segment names an object member, or a list item by its 0-based position (`sources.0.label`), except inside `steps`, where the second segment names a step by its `id` and the rest is a member path within that step (`steps.s3.op`, `steps.s3.inputs.numerator.label`). Two paths name a part of the step list instead: `steps.source_pipeline`, when a source's pipeline steps are unknown and left out of the list, and `steps.version`, when the version's own steps are unknown and left out of it. A gap that names a step must name one in `steps`. A member served as `null` reads two ways: with its path in `gaps` it is unknown, and with no gap the data declares none. An `about.unit` of `null` with an `about.unit` gap is a unit nobody recorded, and without the gap the data has no unit.
- `built_from` names the facts schema version and the `voc_` vocabulary revision.
- A card description composed per request carries `rendering: "live"` and the `frame` it rendered on. Only a card revision carries them.
- A source card's description is composed from the documents of the sources it lists: `about.name` names them, joined and clipped to 512 characters with an ellipsis, `sources` holds each one's document fields and full name, `freshness` when each was downloaded and its next release, `steps` is empty, and `cite` names the card and the sources.

## Vector suite

`src/dfo_descriptions/vectors/` ships with the package as the contract's language-neutral conformance suite. Its README defines the record format.

```bash
# Validate the schemas, the operation list and the vectors, without importing this package
uv run ../../tools/validate_descriptions_vectors.py

# Drive the schemas and check_steps against every vector
uv run pytest tests/test_vectors.py
```
