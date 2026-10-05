# tools

Codegen and drift-check scripts (e.g. exporting `schemas/` from the pydantic models, checking generated artifacts against their committed source).

`validate_registry.py` validates `registries/codes.json` against `schemas/registry/code-registry.schema.json` (which enforces field shapes, the lower-snake-case code pattern, and the closed endpoint/door/channel vocabulary documented in `schemas/registry/README.md`) and checks what a JSON Schema cannot express on its own: code uniqueness, sort order, and classification/channel consistency. It is a PEP 723 script. Run it with:

```bash
uv run tools/validate_registry.py
```

`validate_vectors.py` validates every file under `vectors/identity/` against `schemas/vectors/identity-vector.schema.json` (which enforces field shapes, the positive/negative structural split, and the closed `failure_category` vocabulary documented in `schemas/vectors/README.md`) and checks what a JSON Schema cannot express on its own: that each file's name matches its own `name` field, that every `name` is unique across the suite (`spec_id` is deliberately **not** required unique, the `facets-explicit-empty-object-converges`/`facets-omitted-member-converges` pair legitimately shares one, proving two spellings converge on one id), and, for every positive vector, that `canonical_bytes` is genuinely `JCS(expected_projection)` and `spec_id` is genuinely `"spec_"` plus the first 32 lowercase hex characters of `SHA-256(canonical_bytes)`. It does not re-implement the identity algorithm's normalisation steps, so it does not check `expected_projection` against `input`/`input_bytes`. That is the reference implementation's job, not this fixture-format validator's. Also a PEP 723 script:

```bash
uv run tools/validate_vectors.py
```

`validate_provenance_docs_vectors.py` validates every vector under `python/provenance-docs/src/dfo_provenance_docs/vectors/` against `provenance-docs-vector.schema.json` beside them, and checks what the schema cannot express: file names match ids, ids are unique, the suite meets its floor, every reason in the schema's vocabulary has a rejection vector, and every function has an accepting vector. It does not import `dfo_provenance_docs`. Also a PEP 723 script:

```bash
uv run tools/validate_provenance_docs_vectors.py
```

CI runs every validator on every push and pull request against `main` (see `.github/workflows/ci.yml`'s `registry` job).
