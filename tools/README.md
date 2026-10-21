# tools

Validators for the registries and the conformance vector suites. Each script checks committed JSON against its schema and against the rules the schema cannot express. None of them generates files or imports the package whose vectors it checks. Their tests are in `tools/tests`.

`validate_registry.py` validates `registries/codes.json` against `schemas/registry/code-registry.schema.json` (which enforces field shapes, the lower-snake-case code pattern, and the closed endpoint/door/channel vocabulary documented in `schemas/registry/README.md`) and checks what a JSON Schema cannot express on its own: code uniqueness, sort order, and classification/channel consistency. It is a PEP 723 script. Run it with:

```bash
uv run tools/validate_registry.py
```

`validate_unit_registry.py` validates `registries/units.json` against `schemas/registry/unit-registry.schema.json` (closed rows per kind, member types and the `format_version` pin) and checks what the schema cannot express: rows sorted by `ref` with each `ref` once, `currency_codes` sorted, no row that also matches a currency prefix rule, and byte-identity with the packaged mirror. It does not import `dfo_spec_kit`. Also a PEP 723 script:

```bash
uv run tools/validate_unit_registry.py
```

`validate_vectors.py` validates every file under `vectors/identity/` against `schemas/vectors/identity-vector.schema.json` (which enforces field shapes, the positive/negative structural split, and the closed `failure_category` vocabulary documented in `schemas/vectors/README.md`) and checks what a JSON Schema cannot express on its own: that each file's name matches its own `name` field, that every `name` is unique across the suite (`spec_id` is deliberately **not** required unique, the `facets-explicit-empty-object-converges`/`facets-omitted-member-converges` pair legitimately shares one, proving two spellings converge on one id), and, for every positive vector, that `canonical_bytes` is genuinely `JCS(expected_projection)` and `spec_id` is genuinely `"spec_"` plus the first 32 lowercase hex characters of `SHA-256(canonical_bytes)`. It does not re-implement the identity algorithm's normalisation steps, so it does not check `expected_projection` against `input`/`input_bytes`. That is the reference implementation's job, not this fixture-format validator's. Also a PEP 723 script:

```bash
uv run tools/validate_vectors.py
```

`validate_frame_vectors.py` validates every file under `python/frame-grammar/src/dfo_frame_grammar/vectors/frame/` against `frame-vector.schema.json` beside them, and checks what the schema cannot express: file names match names, names are unique, the suite meets its floor, both classifications are non-empty, vectors over one frame digest share one token and vectors over different digests mint different tokens, and every positive vector's stored bytes, digests and `content_version` are self-consistent. It never imports `dfo_frame_grammar`. `python/frame-grammar/tests/test_vectors.py` drives the real implementation. Also a PEP 723 script:

```bash
uv run tools/validate_frame_vectors.py
```

`validate_provenance_docs_vectors.py` validates every vector under `python/provenance-docs/src/dfo_provenance_docs/vectors/` against `provenance-docs-vector.schema.json` beside them, and checks what the schema cannot express: file names match ids, ids are unique, the suite meets its floor, every reason in the schema's vocabulary has a rejection vector, and every function has an accepting vector. It does not import `dfo_provenance_docs`. Also a PEP 723 script:

```bash
uv run tools/validate_provenance_docs_vectors.py
```

`validate_descriptions_vectors.py` validates the step, description and operation schemas under `python/descriptions/src/dfo_descriptions/schemas/`, checks `operations.json` against its schema and for what the schema cannot express (each op once, the nine analysis operations, each reason once per op, roles exactly when an op takes them), and checks every vector under `python/descriptions/src/dfo_descriptions/vectors/` against `descriptions-vector.schema.json` and its input against the schema it names, with the verdict the vector states. It also checks file names, id uniqueness, the suite's floor, a rejection vector per structure reason, and an accepting and a rejecting vector per schema. It does not import `dfo_descriptions`, so the structure rules are checked by that package's `tests/test_vectors.py`. Also a PEP 723 script:

```bash
uv run tools/validate_descriptions_vectors.py
```

`validate_client_contract.py` validates the client contract under `python/client-contract/src/dfo_client_contract/` against its schemas, then checks what they cannot express: every `strict-fields.json` pointer resolves in `served/openapi.json` and stays required and non-nullable, or keeps its enum values; `actions.json` holds one row, in code order, for each active refusal code in `registries/codes.json` that reaches a problem document or a no_data reason, with a gap kind exactly for the no_data reasons; `client-codes.json` lists each code a client raises itself once, in code order, none of them a code in `actions.json` or the registry, each with a class a scenario names; each recorded scenario's requests match a served route and its JSON bodies validate against that route's schemas, a required member that admits null allowed to be absent; each scenario's `expect` equals the outcomes its steps reach over its exchanges, with refusals' error classes and gap kinds from `actions.json` and each error the client raises itself carrying its code and class from `client-codes.json`, each build posts the `input.scope` its step's narrowing derives over the spec's bound roles, and each Search request posts the input its steps derive; every absolute URL a record holds has the redacted query; and each `DatasetRef` example gets the verdict it states. It does not import `dfo_client_contract`, whose `tests/test_rules.py` replays the rule vectors. Also a PEP 723 script:

```bash
uv run tools/validate_client_contract.py
```

`capture_client_contract.py` refreshes the client contract from dfo-apis. `openapi --dfo-apis PATH` builds the composed app in that checkout's environment, in process, and writes `served/openapi.json` and `served/SOURCE.json`, refusing a checkout with uncommitted changes or untracked files and an environment out of step with its `uv.lock`; `record` runs the contract's scenarios against a base URL, dev by default, writes the records with every absolute URL's query redacted, derives the synthetic ones and validates the result, and `record --offline` derives the outcomes again from the records with no request. It imports its outcome rules from `validate_client_contract.py`. `python/client-contract/README.md` has the recipe. A PEP 723 script; its tests run without network or pyarrow:

```bash
uv run tools/capture_client_contract.py record
```

`validate_unit_vectors.py` validates every file under `vectors/units/` against `schemas/vectors/unit-vector.schema.json`, and checks what the schema cannot express: file names match names, names are unique, the suite meets its floor, and every classification, compare outcome and typed-unit kind has a vector. It does not import `dfo_spec_kit`. Also a PEP 723 script:

```bash
uv run tools/validate_unit_vectors.py
```

The `registry` job in `.github/workflows/ci.yml` runs all of them and `pytest tools/tests`. It runs on every pull request into `dev` or `main` and on every push to either branch. A draft pull request runs no jobs until it is marked ready for review.
