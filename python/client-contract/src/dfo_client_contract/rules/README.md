# Client rules

Every DFO client implements these rules the same way, so a key, a credentials file and a cache directory work across languages. Each rule's vectors are in `<rule>.json`, validated by `rules.schema.json`. In a vector, `env` lists the environment variables set, and an empty value counts as unset everywhere below.

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

## Key format (`key-format`)

A secret key is `dfo_k_`, then a base62 body, then a 6-character checksum; `dfo_p_` marks a browser-safe key. Base62 is `0-9A-Za-z` in that order. The checksum is the CRC32 (ISO-HDLC, as zlib computes it) of the body's ASCII bytes, written in base62 and left-padded with `0` to six characters. Issued keys have a 32-character body, 44 characters in all, but the check ignores length. The last six characters after the prefix are the checksum and the rest is the body.

The check runs in order and reports the first failure: `unknown_prefix` when the key starts with neither prefix, `not_base62` when a character after the prefix is outside base62, and `checksum_mismatch` when the body is empty or its checksum differs. A failure warns with the code `key_<reason>` from `warning-codes.json`, such as `key_checksum_mismatch`, and the client sends the key anyway, so a change of key format forces no upgrade.

## Cache directory (`cache-dir`)

The cache root is the first of, as readerkit's `resolve_cache_dir(app="dfo", ...)` resolves it:

1. the `cache_dir` argument;
1. `DFO_CACHE_DIR`;
1. `BBLOCKS_CACHE_DIR`;
1. the platform cache directory for `readerkit`: on Linux `$XDG_CACHE_HOME/readerkit`, else `~/.cache/readerkit`; on macOS `~/Library/Caches/readerkit`; on Windows `<Local AppData>\readerkit\Cache`, which the vectors give as `LOCALAPPDATA`.

A leading `~` expands to the home directory. The cache directory is `<root>/v1/dfo/cache1`: `v1` is readerkit's own layout version, `dfo` the app, and `cache1` the client cache's layout version, shared by every client and every client release.

## Cache layout (`cache-layout`)

Inside the cache directory:

- a downloaded artifact is `files/sha256/<hex>`, keyed by its digest;
- a manifest is `manifests/<spec_id>/<content_version>/<rendering>.json`, where `<rendering>` is the format, then `-bundle` for a bundle, then `-frame` for the frame layout: `parquet`, `parquet-frame`, `csv-bundle`.

A client writes an entry to a temporary name in the same directory and renames it into place, so a reader never sees a partial entry, and concurrent writers of one entry write identical bytes. A read re-hashes an artifact, and a mismatch counts as a miss.
