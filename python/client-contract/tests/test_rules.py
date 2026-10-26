"""Replay every rule vector through a reference implementation of its rule.

The rules are stated in `dfo_client_contract/rules/README.md`; each function
here is the shortest faithful reading of one of them, so a vector that
disagrees with its rule fails here before a client replays it.
"""

from __future__ import annotations

import ipaddress
import ntpath
import posixpath
import re
import tomllib
import zlib
from typing import Any
from urllib.parse import urlsplit

import pytest

from dfo_client_contract import RULES, rule

_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_KEY_PREFIXES = ("dfo_k_", "dfo_p_")
_DEFAULT_BASE_URL = "https://developmentfinance.org/api/v1"
_TABLE = re.compile(r"\[([A-Za-z0-9_-]+)\]")
_ENTRY = re.compile(r'([A-Za-z0-9_-]+)[ \t]*=[ \t]*"([^"\\\x00-\x1f\x7f]*)"')
_MAJOR_MINOR = re.compile(r"([0-9]+)\.([0-9]+)")


class RuleError(Exception):
    def __init__(
        self, reason: str, line: int | None = None, names: tuple[str, ...] = ()
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.line = line
        self.names = names


def _vectors(name: str) -> list[dict[str, Any]]:
    return rule(name)["vectors"]


def _ids(name: str) -> list[str]:
    return [vector["name"] for vector in _vectors(name)]


def _set(env: dict[str, str], name: str) -> str | None:
    return env.get(name) or None


def _base62(number: int) -> str:
    digits = ""
    while number:
        number, remainder = divmod(number, 62)
        digits = _BASE62[remainder] + digits
    return digits.rjust(6, "0")


def check_key(key: str) -> str | None:
    if not key.startswith(_KEY_PREFIXES):
        return "unknown_prefix"
    rest = key[len(_KEY_PREFIXES[0]) :]
    if any(char not in _BASE62 for char in rest):
        return "not_base62"
    body, checksum = rest[:-6], rest[-6:]
    if not body or _base62(zlib.crc32(body.encode("ascii"))) != checksum:
        return "checksum_mismatch"
    return None


def credentials_path(platform: str, home: str, env: dict[str, str]) -> str:
    if explicit := _set(env, "DFO_CREDENTIALS_FILE"):
        return explicit
    if platform == "windows":
        appdata = _set(env, "APPDATA") or ntpath.join(home, "AppData", "Roaming")
        return ntpath.join(appdata, "dfo", "credentials.toml")
    config = _set(env, "XDG_CONFIG_HOME") or posixpath.join(home, ".config")
    return posixpath.join(config, "dfo", "credentials.toml")


def parse_credentials(text: str) -> dict[str, dict[str, str]]:
    profiles: dict[str, dict[str, str]] = {}
    table: dict[str, str] | None = None
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.removesuffix("\r").strip(" \t")
        if not line or line.startswith("#"):
            continue
        if match := _TABLE.fullmatch(line):
            if match[1] in profiles:
                raise RuleError("duplicate_table", number)
            table = profiles[match[1]] = {}
        elif match := _ENTRY.fullmatch(line):
            if table is None:
                raise RuleError("outside_table", number)
            if match[1] in table:
                raise RuleError("duplicate_key", number)
            table[match[1]] = match[2]
        else:
            raise RuleError("invalid_line", number)
    return profiles


def look_up_key(vector: dict[str, Any]) -> tuple[str, str | None]:
    env = vector["env"]
    api_key = vector.get("api_key")
    if api_key is False:
        return "anonymous", None
    if api_key == "":
        raise RuleError("key_empty")
    if api_key is not None:
        return "argument", api_key
    if from_env := _set(env, "DFO_API_KEY"):
        return "environment", from_env
    if vector["file"] is None:
        return "anonymous", None
    try:
        profiles = parse_credentials(vector["file"])
    except RuleError as error:
        raise RuleError("credentials_file_invalid") from error
    named = _set(env, "DFO_PROFILE")
    profile = profiles.get(named or "default")
    if profile is None:
        if named:
            raise RuleError("profile_not_found")
        return "anonymous", None
    if not profile.get("key"):
        raise RuleError("profile_without_key")
    return "credentials_file", profile["key"]


_NON_DECIMAL_IPV4 = re.compile(r"(?:[^:]*\.)?(?:0x[0-9a-f]*|[0-9]+)")


def is_valid_base_url(url: str) -> bool:
    if re.search(r"[^\x21-\x7e]", url) or "?" in url or "#" in url:
        return False
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or "@" in parts.netloc:
        return False
    if not parts.hostname or {".", ".."} & set(parts.path.split("/")):
        return False
    host = parts.hostname.removesuffix(".")
    if _NON_DECIMAL_IPV4.fullmatch(host):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            return False
    return True


def resolve_base_url(vector: dict[str, Any]) -> tuple[str, str]:
    if (argument := vector.get("base_url")) is not None:
        source, url = "argument", argument
    elif from_env := _set(vector["env"], "DFO_BASE_URL"):
        source, url = "environment", from_env
    else:
        source, url = "default", _DEFAULT_BASE_URL
    if not is_valid_base_url(url):
        raise RuleError("base_url_invalid")
    return source, url.rstrip("/")


def cache_root(vector: dict[str, Any]) -> str:
    platform, home, env = vector["platform"], vector["home"], vector["env"]
    path = ntpath if platform == "windows" else posixpath
    chosen = (
        vector.get("cache_dir")
        or _set(env, "DFO_CACHE_DIR")
        or _set(env, "BBLOCKS_CACHE_DIR")
    )
    if chosen:
        if chosen == "~" or chosen.startswith(("~/", "~\\")):
            chosen = home + chosen[1:]
        return path.normpath(chosen)
    if platform == "windows":
        local = _set(env, "LOCALAPPDATA") or ntpath.join(home, "AppData", "Local")
        return ntpath.join(local, "readerkit", "Cache")
    if platform == "macos":
        return posixpath.join(home, "Library", "Caches", "readerkit")
    xdg = (env.get("XDG_CACHE_HOME") or "").strip() or posixpath.join(home, ".cache")
    return posixpath.join(xdg, "readerkit")


def cache_entry(vector: dict[str, Any]) -> str:
    if vector["entry"] == "file":
        return "files/sha256/" + vector["digest"].removeprefix("sha256:")
    rendering = vector["rendering"]
    key = rendering["format"]
    if rendering["bundle"]:
        key += "-bundle"
    if rendering.get("layout") == "frame":
        key += "-frame"
    return f"manifests/{vector['spec_id']}/{vector['content_version']}/{key}.json"


def reading_order(
    members: list[dict[str, Any]], reading: list[list[str]]
) -> tuple[list[str], list[list[Any]]]:
    """Stacks the members, a row holding null in a column its member lacks,
    then orders the columns and each member's rows."""
    stacked = [
        {**dict(zip(member["columns"], row, strict=True)), "part": member.get("part")}
        for member in members
        for row in member["rows"]
    ]
    served = list(dict.fromkeys(c for member in members for c in member["columns"]))
    first = [
        name
        for names in reading
        if (name := next((n for n in names if n in served), None)) is not None
    ]
    rest = [c for c in served if c not in first and c != "stat_var"]
    varies = {c for c in rest if len({repr(row.get(c)) for row in stacked}) > 1}
    columns = [
        *(["part"] if len(members) > 1 else []),
        *first,
        *[c for c in rest if c in varies],
        *[c for c in rest if c not in varies],
        *(["stat_var"] if "stat_var" in served else []),
    ]
    keys = [c for c in first if c not in ("value", "time_period")]
    keys += [c for c in columns if c in varies]
    if "stat_var" in served and len({repr(row.get("stat_var")) for row in stacked}) > 1:
        keys.append("stat_var")
    keys += ["time_period"] if "time_period" in first else []
    rows: list[list[Any]] = []
    for part in dict.fromkeys(row["part"] for row in stacked):
        block = [row for row in stacked if row["part"] == part]
        block.sort(key=lambda row: [(row.get(k) is None, row.get(k)) for k in keys])
        rows.extend([row.get(c) for c in columns] for row in block)
    return columns, rows


def client_identity(client: str, version: str, runtime: dict[str, str]) -> str:
    label = runtime["name"]
    if label != "browser":
        match = _MAJOR_MINOR.match(runtime["version"])
        assert match, runtime
        label += f" {match[1]}.{match[2]}"
    return f"{client}/{version} ({label})"


def check_caller_headers(headers: dict[str, str]) -> str | None:
    if any(name.lower() == "x-dfo-client" for name in headers):
        return "header_invalid"
    return None


def spec_roles(identity: dict[str, Any]) -> dict[str, list[str] | None]:
    caller = {wire: key for key, wire in rule("role-keys")["keys"].items()}
    flow = rule("role-keys")["flow_keys"]
    bound = {
        caller[role]: dcids for role, dcids in identity.get("entities", {}).items()
    }
    named = [*bound, *(caller[role] for role in identity.get("open_roles", []))]
    if any(key in flow for key in named):
        named.extend(flow)
    return {key: bound.get(key) for key in dict.fromkeys(named)}


def posted_entities(
    identity: dict[str, Any], entities: dict[str, list[str] | None]
) -> dict[str, list[str]]:
    keys = rule("role-keys")["keys"]
    roles = spec_roles(identity)
    for key in entities:
        if roles and key not in roles:
            raise RuleError("invalid_argument", names=(key, *roles))
        if not roles and key in keys.values() and key not in keys:
            caller = next(name for name, wire in keys.items() if wire == key)
            raise RuleError("invalid_argument", names=(key, caller))
    posted = dict(identity.get("entities", {}))
    for key, dcids in entities.items():
        if dcids is None:
            posted.pop(keys.get(key, key), None)
        else:
            posted[keys.get(key, key)] = dcids
    return posted


@pytest.mark.parametrize("name", RULES)
def test_every_rule_has_vectors(name: str) -> None:
    assert rule(name)["rule"] == name
    assert _vectors(name)


@pytest.mark.parametrize("vector", _vectors("key-format"), ids=_ids("key-format"))
def test_key_format(vector: dict[str, Any]) -> None:
    assert check_key(vector["key"]) == vector.get("reason")


@pytest.mark.parametrize(
    "vector", _vectors("credentials-path"), ids=_ids("credentials-path")
)
def test_credentials_path(vector: dict[str, Any]) -> None:
    path = credentials_path(vector["platform"], vector["home"], vector["env"])
    assert path == vector["path"]


@pytest.mark.parametrize(
    "vector", _vectors("credentials-file"), ids=_ids("credentials-file")
)
def test_credentials_file(vector: dict[str, Any]) -> None:
    if "error" in vector:
        with pytest.raises(RuleError) as excinfo:
            parse_credentials(vector["text"])
        assert (excinfo.value.reason, excinfo.value.line) == (
            vector["error"]["reason"],
            vector["error"]["line"],
        )
        return
    assert parse_credentials(vector["text"]) == vector["profiles"]
    # The format is a subset of TOML: any TOML reader reads the same profiles.
    assert tomllib.loads(vector["text"]) == vector["profiles"]


@pytest.mark.parametrize("vector", _vectors("key-lookup"), ids=_ids("key-lookup"))
def test_key_lookup(vector: dict[str, Any]) -> None:
    if "error" in vector:
        with pytest.raises(RuleError) as excinfo:
            look_up_key(vector)
        assert excinfo.value.reason == vector["error"]["reason"]
        return
    assert look_up_key(vector) == (vector["source"], vector["key"])


@pytest.mark.parametrize("vector", _vectors("base-url"), ids=_ids("base-url"))
def test_base_url(vector: dict[str, Any]) -> None:
    if "error" in vector:
        with pytest.raises(RuleError) as excinfo:
            resolve_base_url(vector)
        assert excinfo.value.reason == vector["error"]["reason"]
        return
    assert resolve_base_url(vector) == (vector["source"], vector["url"])


@pytest.mark.parametrize("vector", _vectors("cache-dir"), ids=_ids("cache-dir"))
def test_cache_dir(vector: dict[str, Any]) -> None:
    path = ntpath if vector["platform"] == "windows" else posixpath
    root = cache_root(vector)
    assert root == vector["root"]
    assert path.join(root, "v1", "dfo", "cache1") == vector["directory"]


@pytest.mark.parametrize("vector", _vectors("cache-layout"), ids=_ids("cache-layout"))
def test_cache_layout(vector: dict[str, Any]) -> None:
    assert cache_entry(vector) == vector["path"]


@pytest.mark.parametrize("vector", _vectors("reading-order"), ids=_ids("reading-order"))
def test_reading_order(vector: dict[str, Any]) -> None:
    reading = rule("reading-order")["reading_columns"]
    columns, rows = reading_order(vector["members"], reading)
    assert (columns, rows) == (vector["columns"], vector["rows"])


@pytest.mark.parametrize(
    "vector", _vectors("client-identity"), ids=_ids("client-identity")
)
def test_client_identity(vector: dict[str, Any]) -> None:
    if "headers" in vector:
        assert check_caller_headers(vector["headers"]) == vector.get("reason")
        return
    value = client_identity(vector["client"], vector["version"], vector["runtime"])
    assert value == vector["value"]
    assert re.fullmatch(rule("client-identity")["pattern"], value)


@pytest.mark.parametrize("vector", _vectors("role-keys"), ids=_ids("role-keys"))
def test_role_keys(vector: dict[str, Any]) -> None:
    roles = spec_roles(vector["identity"])
    assert list(roles.items()) == list(vector["roles"].items())
    if "error" in vector:
        with pytest.raises(RuleError) as excinfo:
            posted_entities(vector["identity"], vector["entities"])
        error = excinfo.value
        assert (error.reason, list(error.names)) == (
            vector["error"]["reason"],
            vector["error"]["names"],
        )
    elif "entities" in vector:
        posted = posted_entities(vector["identity"], vector["entities"])
        assert posted == vector["posted"]
