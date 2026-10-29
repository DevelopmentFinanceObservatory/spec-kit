# Client rules

Clients implement these rules for the capabilities available in their runtime. Python implements every rule. TypeScript implements the key, credentials and base-URL rules in Node, and the reading-order, client-identity, role-keys and file-selector rules in every runtime; browser runtimes use the key sources available there. TypeScript currently keeps no disk cache, so `cache-dir` and `cache-layout` apply to Python. Clients implementing the same capability use the same rules and formats. Each rule's vectors are in `<rule>.json`, validated by `rules.schema.json`. In a vector, `env` lists the environment variables set, and an empty value counts as unset everywhere below.

## Credentials path (`credentials-path`)

The credentials file is the first of:

1. `$DFO_CREDENTIALS_FILE`, as given;
1. on Linux and macOS, `$XDG_CONFIG_HOME/dfo/credentials.toml`, else `~/.config/dfo/credentials.toml`;
1. on Windows, `%APPDATA%\dfo\credentials.toml`, else `<home>\AppData\Roaming\dfo\credentials.toml`.

Windows ignores `XDG_CONFIG_HOME`.

## Credentials file (`credentials-file`)

The file is UTF-8 text in a subset of TOML, so any TOML reader reads the same profiles. Lines split on `\n`, and a line drops a final `\r` and its surrounding spaces and tabs. A line is then one of:

- empty, or a comment starting with `#`;
- a table header `[name]`, which opens the profile `name`;
- an entry `name = "value"`, with spaces or tabs allowed around `=`, which sets `name` in the open profile.

A name is one or more of `A-Z`, `a-z`, `0-9`, `_` and `-`. A value holds no `"`, no `\` and no control character, so it needs no escape. A profile keeps every entry it holds; a client reads `key`. The first broken line refuses the file with its 1-based line number and a reason: `outside_table` for an entry before any header, `duplicate_table` for a header seen before, `duplicate_key` for a name its profile already holds, and `invalid_line` for anything else, a trailing comment included. Only the Python CLI writes the file, readable by its owner only.

## Key lookup (`key-lookup`)

A client resolves its key when it is built, and again after a 401. The first source found wins:

1. the `api_key` argument: a key, or `false` to force anonymous use; an empty string raises `key_empty`;
1. `DFO_API_KEY`;
1. the credentials file at the credentials path, profile `$DFO_PROFILE`, else `default`;
1. inside Colab, a notebook secret named `DFO_API_KEY`;
1. otherwise, anonymous.

A missing file goes on to the next source, and so does a missing `default` profile. A file that fails to parse raises `credentials_file_invalid`, a profile named by `DFO_PROFILE` that the file lacks raises `profile_not_found`, and a chosen profile with no `key`, or an empty one, raises `profile_without_key`. The file is read only when no earlier source wins. The argument may also be a callable or a key object, called or unwrapped when the client resolves the key. The vectors pass strings, and the Colab source has no vectors.

## Base URL (`base-url`)

A client resolves its base URL when it is built. The first source found wins:

1. the `base_url` argument of the call or constructor, as given;
1. `DFO_BASE_URL`;
1. the public default, `https://developmentfinance.org/api/v1`.

Only the chosen source is checked. A base URL is valid when it is an absolute `http` or `https` URL with a host and none of these: userinfo, an empty one as in `https://@host/api` included; a query; a fragment; a `.` or `..` path segment, with any of its dots written `%2e` or `%2E` included; a backslash; a port that is not 1 to 65535 in decimal digits, an empty port after a `:` included; a character outside visible ASCII, a control character, a space or a non-ASCII character; or a host written as an IPv4 address in any form but four decimal numbers, such as `127.1`, `0x7f.1` or `2130706433`. An invalid one raises `base_url_invalid`, so an empty `base_url` argument raises it where an empty `DFO_BASE_URL` counts as unset. A relative URL is invalid, except in a TypeScript client inside a browser page, which resolves a relative base URL against the page's address. A valid value is used as given, less its trailing slashes, so the client's base URL reads `https://developmentfinance.org/api/v1` for `https://developmentfinance.org/api/v1/`; the rule fixes the source and the validity, and leaves the rest of the URL's form to the client. A key sent over plain `http` to a host that is not loopback is refused by `insecure_transport`, which this rule does not decide.

## Key format (`key-format`)

A secret key is `dfo_k_`, then a base62 body, then a 6-character checksum; `dfo_p_` marks a browser-safe key. Base62 is `0-9A-Za-z` in that order. The checksum is the CRC32 (ISO-HDLC, as zlib computes it) of the body's ASCII bytes, written in base62 and left-padded with `0` to six characters. Issued keys have a 32-character body, 44 characters in all, but the check ignores length. The last six characters after the prefix are the checksum and the rest is the body.

The check runs in order and reports the first failure: `unknown_prefix` when the key starts with neither prefix, `not_base62` when a character after the prefix is outside base62, and `checksum_mismatch` when the body is empty or its checksum differs. A failure warns with the code `key_<reason>` from `warning-codes.json`, such as `key_checksum_mismatch`, and the client sends the key anyway, so a change of key format forces no upgrade.

## Cache directory (`cache-dir`)

The cache root is the first of, as readerkit's `resolve_cache_dir(app="dfo", ...)` resolves it:

1. the `cache_dir` argument;
1. `DFO_CACHE_DIR`;
1. `BBLOCKS_CACHE_DIR`;
1. the platform cache directory for `readerkit`: on Linux `$XDG_CACHE_HOME/readerkit`, else `~/.cache/readerkit`; on macOS `~/Library/Caches/readerkit`; on Windows `<Local AppData>\readerkit\Cache`, which the vectors give as `LOCALAPPDATA`.

A leading `~` expands to the home directory. The cache directory is `<root>/v1/dfo/cache1`: `v1` is readerkit's own layout version, `dfo` the app, and `cache1` the client cache's layout version. Clients and releases implementing `cache1` share this layout.

## Cache layout (`cache-layout`)

Inside the cache directory:

- a downloaded artifact is `files/sha256/<hex>`, keyed by its digest;
- a manifest is `manifests/<spec_id>/<content_version>/<rendering>.json`, where `<rendering>` is the format, then `-bundle` for a bundle, then `-frame` for the frame layout: `parquet`, `parquet-frame`, `csv-bundle`.

The key leaves out a rendering's `without_party_totals`: a cut that leaves rows out answers a version of its own, and one that leaves none out answers the plain version's file. A client writes an entry to a temporary name in the same directory and renames it into place, so a reader sees a complete entry. Concurrent writers of one content-addressed artifact write identical bytes. Manifest updates can replace another complete manifest and lose a cache candidate, so cache availability is best-effort. A read re-hashes an artifact, and a mismatch counts as a miss.

## Reading order (`reading-order`)

A client presents a dataset's columns and rows in reading order, in every table it returns. `reading_columns` in `reading-order.json` lists nine slots in order: the time period, the provider, the recipient, the subject, the purpose, the value, the unit, the flow type and the provenance. Each slot names the long layout's label column, then the frame layout's code column, and the flow type slot names the CRS pair, then the DAC2a aid type pair. The first name of a slot that the dataset holds fills the slot, and a slot whose names it holds none of stays empty.

| Slot        | Long layout                                      | Frame layout                                           |
| ----------- | ------------------------------------------------ | ------------------------------------------------------ |
| Time period | `time_period`                                    | `time_period`                                          |
| Provider    | `financingProvider_label`                        | `financingProvider`                                    |
| Recipient   | `financingRecipient_label`                       | `financingRecipient`                                   |
| Subject     | `observationAbout_label`                         | `observationAbout`                                     |
| Purpose     | `crsPurpose_label`                               | `stat_var_crsPurpose`                                  |
| Value       | `value`                                          | `value`                                                |
| Unit        | `unit_label`                                     | `unit`                                                 |
| Flow type   | `crsTypeOfFlow_label`, else `dac2aAidType_label` | `stat_var_crsTypeOfFlow`, else `stat_var_dac2aAidType` |
| Provenance  | `provenance_label`                               | `provenance`                                           |

The columns come in four groups:

1. the filled slots, in slot order;
1. the other columns whose values differ between rows, in the order the server wrote them;
1. the other columns that hold one value in every row, null included, in the order the server wrote them;
1. `stat_var`, last whether or not it varies.

A table with no row or one row has no column that varies. Two cells hold the same value when they are equal as JSON values: numbers by value, so `1` and `1.0` are one value, booleans apart from numbers, so `true` and `1` differ, and null the same as null. NaN, which a Parquet double column can hold, is the same as NaN. A list or struct cell is the same as another when their items are the same, in order. A dataset of several members puts `part` first and judges the groups over the stacked table, where a member's rows hold null in a column that member lacks. So a CRS series of Germany's basic health ODA to Nigeria, split by co-operation modality, reads `time_period`, `financingProvider_label`, `financingRecipient_label`, `crsPurpose_label`, `value`, `unit_label`, `crsTypeOfFlow_label` and `provenance_label`, then `crsCooperationModality` and `crsCooperationModality_label`, which tell its rows apart, then its 15 constant columns, `stat_var` last.

The rows of each member are sorted by the filled slots in slot order, leaving out `value`, a measure, and the time period, then by the varying columns in presented order, `stat_var` after them when it varies, then by the time period, so each series' years read together. The keys of that series are the provider, recipient, purpose, unit, flow type and provenance labels, then `crsCooperationModality` and `crsCooperationModality_label`, then the time period, so it reads every A02 year, then every C01 year. Each key sorts ascending, numbers by value and strings by Unicode code point, with nulls last. Rows whose keys tie keep the order the server wrote them. The members follow one another in member order.

A vector gives the dataset's `members`, each with its `part` when there are several, its `columns` and its `rows` as the server wrote them, and the `columns` and `rows` a client presents. Each row holds one cell per column, and the cells of a column, nulls aside, are all strings, all numbers or all booleans, since the rule orders no value against one of another type. The order moves columns and rows and changes no value. A client hashes a downloaded file before it orders anything, so the digest and the ref keep the server's file.

## Client identity (`client-identity`)

Every request a client sends to the API carries `X-DFO-Client: <client>/<version> (<runtime>)`. `<client>` is `dfo-python` or `dfo-ts`, and `<version>` is the package version as the package states it. `<runtime>` names the runtime and its major and minor version. In Python it is the implementation as `platform.python_implementation()` names it, then the interpreter's major and minor version, such as `CPython 3.14`. In TypeScript it is `Node`, `Deno` or `Bun` with the major and minor of that runtime's version, such as `Node 22.18`, `browser` in a browser page or browser worker, and `other` in any other runtime, such as Cloudflare Workers. `pattern` in `client-identity.json` matches every well-formed value. A vector gives the `client`, the `version`, the `runtime` as its `name` and full `version`, or `browser` or `other` with no version, and the `value` the client sends.

An API request is one to the base URL's origin, a `path` or `origin_path` request in a scenario. A request to any other origin, a signed storage URL included, carries no `X-DFO-Client`. In a browser, the files-route request the TypeScript client sends with `redirect: "follow"` carries the API key alone, because the browser forwards every other header to the storage host it is redirected to.

A caller cannot replace or remove the header. A client checks the caller's `headers` argument before any request, and the headers a header provider returns each time the client calls it. A name equal to `X-DFO-Client` in any letter case raises `InvalidRequestError` `header_invalid`, whatever its value, the empty string included. A vector gives `via`, `argument` or `provider`, the caller's `headers`, and the `verdict`, `accept` or `refuse` with the `reason`. Every other header is the caller's to set, `User-Agent` included. Python sends `dfo-python/<version>`, TypeScript sends `dfo-ts/<version>` whenever it runs outside a browser page or browser worker (Node, Deno, Bun, or a server runtime such as Cloudflare Workers) and none inside one, where the browser forbids setting it, and a caller's `User-Agent` replaces the client's.

## Role keys (`role-keys`)

A caller narrows a spec by its entity roles with `entities=`, keyed by the caller keys `keys` in `role-keys.json` lists: `place`, the place a measure is about, and the flow directions `origin` and `destination`. A client posts `place` as the wire role `observationAbout` and each direction as itself. `Spec.roles` in Python, `specRoles(spec)` in TypeScript, and every error a client raises name the caller key. Each role has one caller spelling, so `observationAbout` is a wire role and no caller key.

The roles a spec takes are those its `definition.identity` binds in `entities` or lists in `open_roles`, in that order, each under its caller key with the dcids the spec binds it to, or null when it binds none. A flow spec, one that names `origin` or `destination`, takes both, since a direction it neither binds nor opens reads as the total over it. The direction it leaves unnamed comes last. A spec that names no role takes `{}`.

A client checks each key the caller types before any request. A key outside the spec's roles raises `InvalidRequestError` `invalid_argument`, and the message names the key and the spec's roles. So GDP for Kenya refuses `origin` and `observationAbout` naming `place`, and a flow spec refuses `place` and a column name such as `financingRecipient` naming `origin` and `destination`. A spec that names no role leaves the check to the server, since the client cannot tell which roles it takes. Such a spec posts `place` as `observationAbout` and any other key as typed, and still refuses `observationAbout` naming `place`.

The server replaces a spec's whole binding with the posted one, so a client posts the spec's own `identity.entities` with each role the caller names replaced by its dcids and each role the caller binds to null dropped. A vector gives the spec's `identity` and its `roles`. A vector that narrows adds the caller's `entities`, then either `posted`, the wire entities the build posts, or the `error` with its `reason` and the keys its message `names`, the typed key first.

## File selector (`file-selector`)

The file selector, `GET /datasets/{spec_id}/{content_version}/file`, redirects to a stored version's file in a rendering. A client asks it for a ref member's version when the files route at the stored digest refuses, or when the snapshot manifest lists no artifact of that digest. Its query holds the keys `query_keys` in `file-selector.json` lists, `format`, `bundle` and `layout`, in that order, each one the member's rendering holds, as `key=value` pairs joined by `&`, with booleans written `true` and `false`. The query leaves out `without_party_totals`: the version records the cut it was minted with, and the server lays that over the query. The server refuses any other key with 400 `request_invalid`, so a selector query that carried the cut would end the lookup. A vector gives the `rendering` and the `query` a client sends, without the leading `?`.
