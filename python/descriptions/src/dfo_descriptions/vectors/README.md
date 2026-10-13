# python/descriptions/src/dfo_descriptions/vectors

This is the step and description vector suite. `descriptions-vector.schema.json` (draft 2020-12) validates every record in this directory. The suite ships as package data, so an installed `dfo_descriptions` always carries it.

## Format

Each vector is one JSON file named `<id>.json`, holding one record.

| Field         | Meaning                                                                                                                                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`          | Stable, unique, kebab-case id. Matches the file name stem.                                                                                                                                                          |
| `description` | One sentence stating what the vector proves.                                                                                                                                                                        |
| `schema`      | `steps` or `description`: the schema `input` is checked against.                                                                                                                                                    |
| `input`       | The step list or description under test.                                                                                                                                                                            |
| `expect`      | `"accept"`, when the input passes its schema and the structure rules.                                                                                                                                               |
| `error`       | `{"by": "schema"}` when the schema rejects the input, or `{"by": "structure", "reason", "step"}` when it passes the schema and breaks a structure rule. `step` is 0-based, or `null` for a bound on the whole list. |

A record carries exactly one of `expect` and `error`. A `description` vector runs the structure rules on its `steps` when they are a list, then on its caveats and gaps.

## Coverage

The suite holds a dataset version's worked-example description, an `ops_` description of a quotient of two datasets, a card revision and its live rendering, each carrying its measure, fixed values and drawn values with definitions, a card without a measure, that quotient written with reason names the operation list lacks (accepted, since a reason is a bounded string), each kind of operation, the size bound counted on the stored list in JCS and left unchecked on served steps, and at least one rejection per structure reason. The worked example's two leading providers and their counts are illustrative.

`tools/validate_descriptions_vectors.py` checks the record schema, file names, id uniqueness, a floor on the suite's size, each input's schema verdict, that every structure reason has a rejection vector, and that each schema has an accepting and a rejecting vector. It does not import the package. `tests/test_vectors.py` also runs `check_steps` and `check_description`.

## Adding a vector

Write the input, decide the verdict from the schemas and the rules in the package README, and run both validators.
