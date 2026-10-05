# python/provenance-docs/src/dfo_provenance_docs/vectors

This is the provenance documentation grammar's vector suite. `provenance-docs-vector.schema.json` (draft 2020-12) validates every record in this directory. The suite ships as package data, so an installed `dfo_provenance_docs` always carries it.

The expected values are written by hand from the templates and rules in the package README, then checked against the implementation by `tests/test_vectors.py`.

## Format

Each vector is one JSON file named `<id>.json`, holding one record.

| Field         | Meaning                                                                                                     |
| ------------- | ----------------------------------------------------------------------------------------------------------- |
| `id`          | Stable, unique, kebab-case id. Matches the file name stem.                                                  |
| `description` | One sentence stating what the vector proves.                                                                |
| `function`    | `decode_mcf_text`, `parse_transformations`, `parse_changelog`, `parse_prose` or `parse_notes`.              |
| `input`       | The property value as the graph stores it: the body of a JSON string, with every quote written as `\u0022`. |
| `expect`      | The result, when the input is accepted.                                                                     |
| `error`       | `{"reason", "line"}`, when the input is rejected. `line` is 1-based in the decoded document, or `null`.     |

A record carries exactly one of `expect` and `error`. A `decode_mcf_text` vector runs the decoder on `input`. Every other vector decodes `input` first and runs the named parser on the result, so a document vector can also fail as `undecodable`.

`expect` takes the shape of the function's result:

| Function                | `expect`                                                                              |
| ----------------------- | ------------------------------------------------------------------------------------- |
| `decode_mcf_text`       | a string                                                                              |
| `parse_transformations` | `[{"title", "description"}]`                                                          |
| `parse_changelog`       | `[{"date": "YYYY-MM-DD", "title", "changes": [{"kind", "note"}]}]`, `kind` lower case |
| `parse_prose`           | a string                                                                              |
| `parse_notes`           | `[string]`                                                                            |

## Coverage

The suite holds the eight encode vectors from the import pipeline's own tests as `decode-*` records, the CRS transformations document, the live CRS changelog (rejected as `missing_release_title`, since its headings carry no titles), the template examples, and at least one rejection per reason. Every document input equals the pipeline's encoding of its decoded text, which `tests/test_vectors.py` checks with a copy of the pipeline's encoder.

`tools/validate_provenance_docs_vectors.py` checks the schema, file names, id uniqueness, a floor on the suite's size, and that every reason has a rejection vector and every function an accepting one. It does not import the package. CI runs it and the package tests on every push and pull request.

## Adding a vector

Write the Markdown, encode it with the pipeline's `escape_markdown`, drop the outer quotes, and store the result as `input`. Write `expect` or `error` from the rules, not from the implementation's output. Run both validators.
