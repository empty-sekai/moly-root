"""Built-in Japanese-server manifest discovery and scoped bundle planning.

Protocol shape is adapted from mos9527/sssekai 0.8.0 at commit
523a3659ea6641a6aa9c5f940341ad458391344c (MIT).  Network activity is explicit:
importing this module never opens a connection.
"""
from __future__ import annotations

from base64 import urlsafe_b64decode
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .atomic import PRIVATE_FILE_MODE, PUBLIC_FILE_MODE, write_json
from .fetch import Manifest, flatten

BOOTSTRAP_URL = "https://raw.githubusercontent.com/Sekai-World/sekai-master-db-diff/main/versions.json"
GAME_VERSION_HOST = "game-version.sekai.colorfulpalette.org"
ISSUE_HOST = "issue.sekai.colorfulpalette.org"
DEFAULT_API_HOST = "production-game-api.sekai.colorfulpalette.org"
API_KEY = b"g2fcC0ZczN9MTJ61"
API_IV = b"msx3IV0i9XE5uYZ1"
UPSTREAM_COMMIT = "523a3659ea6641a6aa9c5f940341ad458391344c"


class JPError(RuntimeError):
    """Actionable JP discovery, protocol, or identity failure."""


# Published provenance is an identity projection, never a copy of a local
# extraction journal. Keep relative resource names and content hashes, but no
# workspace names, machine paths, command lines or arbitrary extension fields.
_SOURCE_IDENTITY = {key: "atom" for key in (
    "region", "appVersion", "appHash", "assetVersion", "assetHash", "platform",
    "profile", "assetbundleHostHash", "manifestVersion", "versionSource")}
_SOURCE_IDENTITY["upstreamReference"] = {"project": "atom", "commit": "atom"}
_MASTER_IDENTITY = {key: "atom" for key in (
    "region", "commit", "assetVersion", "assetHash", "dataVersion", "repository", "provider")}
_FILE_IDENTITY = {"path": "path", "sha256": "sha256"}
_SOURCE_DOCUMENT = {
    **{key: "atom" for key in ("version", "kind", "runtimeStatus", "validationStatus",
                               "coordinateContract", "coordinateUnits")},
    "descriptorSha256": "sha256", "source": _SOURCE_IDENTITY, "master": _MASTER_IDENTITY,
    "sharedUi": {
        "purpose": "atom", "donorRegion": "atom", "donorVersion": "atom",
        "files": [_FILE_IDENTITY],
        "regionUi": {
            "directory": "path", "region": "atom", "clientVersion": "atom", "unityVersion": "atom",
            "files": [_FILE_IDENTITY],
            "replacesDonor": [{"document": "path", "donorCopy": "path",
                               "removedDonorFiles": [_FILE_IDENTITY]}],
        },
    },
}
_LOCAL_REFERENCE = re.compile(r"(?:^|[\s\"'])(?:[A-Za-z]:|[/\\]|~[/\\]|file:)", re.IGNORECASE)


def _public_projection(value, schema, label):
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be a provenance object")
        return {key: _public_projection(value[key], child, f"{label}.{key}")
                for key, child in schema.items() if key in value}
    if isinstance(schema, list):
        if not isinstance(value, list):
            raise ValueError(f"{label} must be a provenance array")
        return [_public_projection(row, schema[0], f"{label}[]") for row in value]
    if schema == "sha256":
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError(f"{label} must be a SHA-256 identity")
    elif schema == "path":
        if (not isinstance(value, str) or not value or "\\" in value or ":" in value
                or any(part in ("", ".", "..") for part in value.split("/"))):
            raise ValueError(f"{label} must be a relative resource path")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise ValueError(f"{label} must be a scalar source identity")
    if isinstance(value, str) and _LOCAL_REFERENCE.search(value):
        raise ValueError(f"{label} contains a machine-specific reference")
    return value


def public_source_document(document):
    """Portable runtime source metadata; coordinate and source readers remain intact."""
    return _public_projection(document, _SOURCE_DOCUMENT, "source.json")


def public_release_provenance(provenance):
    """Allowlisted release identity, including explicit CLI provenance inputs."""
    return _public_projection(provenance, {
        "runtime_source": _SOURCE_DOCUMENT, "source": _SOURCE_IDENTITY,
        "master": _MASTER_IDENTITY, "descriptorSha256": "sha256",
        "asset_version": "atom", "asset_hash": "atom", "master_commit": "atom",
    }, "release provenance")


def _validate_segment(label, value, pattern=r"[A-Za-z0-9._-]+"):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"invalid JP {label}: {value!r}")
    return value


def _crypto():
    try:
        from Crypto.Cipher import AES
        import msgpack
    except ImportError as exc:
        raise JPError("JP live discovery needs the 'jp' extra: pip install moly-root[jp]") from exc
    return AES, msgpack


def _encrypt(document):
    AES, msgpack = _crypto()
    body = msgpack.packb(document, use_single_float=True)
    pad = 16 - len(body) % 16
    return AES.new(API_KEY, AES.MODE_CBC, API_IV).encrypt(body + bytes([pad]) * pad)


def _decrypt(body):
    AES, msgpack = _crypto()
    if not body or len(body) % 16:
        raise JPError("JP response is not an AES-CBC block stream")
    plain = AES.new(API_KEY, AES.MODE_CBC, API_IV).decrypt(body)
    pad = plain[-1]
    if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        raise JPError("JP response has invalid PKCS#7 padding")
    return msgpack.unpackb(plain[:-pad], raw=False, strict_map_key=False)


def _jwt_user_id(credential):
    try:
        payload = credential.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        value = json.loads(urlsafe_b64decode(payload).decode("utf-8"))["userId"]
        return int(value)
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise JPError("JP credential is not a supported user credential") from exc


class Client:
    """Small explicit JP API client; secrets never appear in errors or reports."""

    def __init__(self, app_version, app_hash, *, platform="android",
                 unity_version="2022.3.62f3", timeout=30.0, retries=3):
        self.app_version = _validate_segment("app version", app_version)
        self.app_hash = _validate_segment("app hash", app_hash)
        if platform not in ("android", "ios"):
            raise ValueError("JP platform must be 'android' or 'ios'")
        self.platform = platform
        self.unity_version = _validate_segment("Unity version", unity_version)
        if timeout <= 0 or retries < 1:
            raise ValueError("timeout must be positive and retries must be at least one")
        self.timeout, self.retries = timeout, retries
        self.cookie = None
        self.api_host = DEFAULT_API_HOST
        self.profile = "production"
        self.host_hash = None

    def headers(self, *, session_token=None):
        label = "Android" if self.platform == "android" else "iOS"
        values = {"Accept": "application/octet-stream", "Content-Type": "application/octet-stream",
                  "Accept-Encoding": "identity", "User-Agent": f"UnityPlayer/{self.unity_version}",
                  "X-Platform": label, "X-DeviceModel": "moly-root/jp",
                  "X-OperatingSystem": label, "X-Unity-Version": self.unity_version,
                  "X-App-Version": self.app_version, "X-App-Hash": self.app_hash}
        if self.cookie:
            values["Cookie"] = self.cookie
        if session_token:
            values["X-Session-Token"] = session_token
        return values

    def request(self, method, url, *, document=None, session_token=None, packed=True):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        data = _encrypt(document) if document is not None else (b"" if method != "GET" else None)
        last = None
        attempted = 0
        parsed = urlsplit(url)
        safe_url = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        for attempt in range(self.retries):
            attempted += 1
            try:
                with build_opener(NoRedirect()).open(
                        Request(url, method=method, data=data,
                                headers=self.headers(session_token=session_token)),
                        timeout=self.timeout) as response:
                    limit = 256 * 1024 * 1024
                    body = response.read(limit + 1)
                    if len(body) > limit:
                        raise JPError("JP metadata response exceeds the 256 MiB safety limit")
                    length = response.headers.get("Content-Length")
                    if length is not None and (not length.isdigit() or int(length) != len(body)):
                        raise JPError("JP metadata response length does not match Content-Length")
                    return (_decrypt(body) if packed else body), response.headers
            except HTTPError as exc:
                last = JPError(f"JP service returned HTTP {exc.code} for {method} {safe_url}")
                if 300 <= exc.code < 500:
                    break
            except (URLError, TimeoutError, OSError, JPError) as exc:
                last = exc
            if attempt + 1 < self.retries:
                time.sleep(min(2 ** attempt, 4))
        raise JPError(f"JP request failed after {attempted} attempt(s): {last}") from last

    def issue_signature(self):
        _, headers = self.request("POST", f"https://{ISSUE_HOST}/api/signature", packed=False)
        cookie = headers.get("Set-Cookie")
        if not cookie:
            raise JPError("JP signature endpoint returned no Set-Cookie header")
        self.cookie = cookie

    def game_version(self):
        data, _ = self.request("GET", f"https://{GAME_VERSION_HOST}/{self.app_version}/{self.app_hash}")
        self.profile = _validate_segment("profile", data["profile"], r"[a-z0-9-]+")
        self.host_hash = _validate_segment("asset host hash", data["assetbundleHostHash"], r"[a-f0-9]+")
        domain = data["domain"].replace("{0}", self.profile)
        if not re.fullmatch(r"[A-Za-z0-9.-]+", domain) or not domain.endswith(".sekai.colorfulpalette.org"):
            raise JPError("JP game-version response contains an untrusted API domain")
        self.api_host = domain
        return data

    def system(self):
        return self.request("GET", f"https://{self.api_host}/api/system")[0]

    def register_guest(self):
        label = "Android" if self.platform == "android" else "iOS"
        data, _ = self.request("POST", f"https://{self.api_host}/api/user",
                               document={"platform": label, "deviceModel": "moly-root/jp",
                                         "operatingSystem": label})
        return {"credential": data["credential"]}

    def authenticate(self, credential):
        user_id = _jwt_user_id(credential)
        data, _ = self.request("PUT", f"https://{self.api_host}/api/user/{user_id}/auth",
                               document={"credential": credential, "deviceId": None})
        return data

    def manifest(self, asset_version, asset_hash, *, session_token=None):
        asset_version = _validate_segment("asset version", asset_version)
        asset_hash = _validate_segment("asset hash", asset_hash)
        if not self.host_hash:
            raise JPError("game-version discovery must precede manifest discovery")
        host = f"{self.profile}-{self.host_hash}-assetbundle-info.sekai.colorfulpalette.org"
        url = f"https://{host}/api/version/{asset_version}/{asset_hash}/os/{self.platform}"
        return self.request("GET", url, session_token=session_token)[0]


def _bootstrap(url=BOOTSTRAP_URL, *, timeout=30.0):
    """Read public version metadata in maintained camelCase or legacy snake_case shape."""
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with build_opener(NoRedirect()).open(
                Request(url, headers={"User-Agent": "moly-root/jp"}), timeout=timeout) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise JPError("JP version metadata exceeds the 1 MiB safety limit")
            data = json.loads(body.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JPError(f"JP version metadata request failed ({type(exc).__name__})") from exc
    row = data.get("production_android", data)
    result = {
        "appVersion": row.get("appVersion", row.get("app_version")),
        "appHash": row.get("appHash", row.get("app_hash")),
        "assetVersion": row.get("assetVersion", row.get("asset_version")),
        "assetHash": row.get("assetHash", row.get("asset_hash")),
        "status": row.get("appVersionStatus"),
    }
    _validate_segment("app version", result["appVersion"])
    _validate_segment("app hash", result["appHash"])
    for key in ("assetVersion", "assetHash"):
        if result[key] is not None:
            _validate_segment(key, result[key])
    return result


def discover(*, bootstrap_url=BOOTSTRAP_URL, timeout=30.0, retries=3, unity_version="2022.3.62f3"):
    """Probe public version metadata and confirm it against official services."""
    boot = _bootstrap(bootstrap_url, timeout=timeout)
    client = Client(boot["appVersion"], boot["appHash"], timeout=timeout,
                    retries=retries, unity_version=unity_version)
    client.issue_signature(); game = client.game_version(); system = client.system()
    versions = system.get("appVersions") or []
    available = [row for row in versions if row.get("appVersionStatus") == "available"]
    current = available[-1] if available else (versions[-1] if versions else None)
    return {"version": 1, "region": "jp", "versionSource": "Sekai-World/sekai-master-db-diff",
            "bootstrap": boot, "current": current,
            "bootstrapCurrent": bool(current and current.get("appVersion") == boot["appVersion"]),
            "profile": game["profile"], "assetbundleHostHash": game["assetbundleHostHash"],
            "upstreamReference": {"project": "mos9527/sssekai", "commit": UPSTREAM_COMMIT}}


def fetch_manifest(output, *, app_version=None, app_hash=None, asset_version=None,
                   asset_hash=None, versions_url=BOOTSTRAP_URL, guest=False,
                   session_file=None, unity_version="2022.3.62f3", timeout=30.0,
                   retries=3):
    """Atomically publish a JP descriptor; guest account creation is opt-in only."""
    public = (_bootstrap(versions_url, timeout=timeout)
              if not all((app_version, app_hash, asset_version, asset_hash)) else {})
    app_version = app_version or public["appVersion"]
    app_hash = app_hash or public["appHash"]
    asset_version = asset_version or public.get("assetVersion")
    asset_hash = asset_hash or public.get("assetHash")
    client = Client(app_version, app_hash, unity_version=unity_version,
                    timeout=timeout, retries=retries)
    client.issue_signature(); game = client.game_version(); system = client.system()
    available = {row["appVersion"] for row in system.get("appVersions", [])
                 if row.get("appVersionStatus") == "available"}
    if available and app_version not in available:
        raise JPError(f"JP app version {app_version} is not available; server accepts {sorted(available)}")
    session_token = None
    if not asset_version or not asset_hash:
        if not guest:
            raise JPError("public metadata lacks asset identity; pass --asset-version/--asset-hash or explicitly use --guest")
        if session_file is None:
            raise JPError("--guest requires --session for private local credential storage")
        session_path = Path(session_file)
        try:
            session = json.loads(session_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            session = client.register_guest()
            write_json(session_path, {"version": 1, "region": "jp", **session}, mode=PRIVATE_FILE_MODE)
        if session.get("region", "jp") != "jp" or not session.get("credential"):
            raise JPError("session file is not a JP credential document")
        auth = client.authenticate(session["credential"])
        asset_version, asset_hash = auth["assetVersion"], auth["assetHash"]
        session_token = auth["sessionToken"]
    manifest = client.manifest(asset_version, asset_hash, session_token=session_token)
    source = {"region": "jp", "appVersion": app_version, "appHash": app_hash, "assetVersion": asset_version,
              "assetHash": asset_hash, "platform": client.platform,
              "profile": client.profile, "assetbundleHostHash": client.host_hash,
              "manifestVersion": manifest.get("version"),
              "versionSource": "Sekai-World/sekai-master-db-diff",
              "upstreamReference": {"project": "mos9527/sssekai", "commit": UPSTREAM_COMMIT}}
    asset_base = (f"https://{client.profile}-{client.host_hash}-assetbundle.sekai.colorfulpalette.org/"
                  f"{asset_version}/{asset_hash}/{client.platform}")
    descriptor = {"version": manifest.get("version"), "source": source,
                  "assetBaseUrl": asset_base, "bundles": manifest.get("bundles", {})}
    write_json(output, descriptor, mode=PUBLIC_FILE_MODE)
    return descriptor


def load_descriptor(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    source = data.get("source") or {}
    if source.get("region") != "jp":
        raise JPError("descriptor source.region must be 'jp'; CN/JP caches cannot be merged")
    if not source.get("assetVersion") or not source.get("assetHash"):
        raise JPError("descriptor lacks JP assetVersion/assetHash identity")
    base = data.get("assetBaseUrl")
    if not isinstance(base, str):
        raise JPError("descriptor lacks a secure JP assetBaseUrl")
    parsed = urlsplit(base)
    version = _validate_segment("asset version", source["assetVersion"])
    asset_hash = _validate_segment("asset hash", source["assetHash"])
    platform = source.get("platform", "android")
    if platform not in ("android", "ios"):
        raise JPError("descriptor platform must be android or ios")
    expected_path = f"/{version}/{asset_hash}/{platform}"
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or parsed.query or parsed.fragment or parsed.path.rstrip("/") != expected_path
            or not re.fullmatch(r"[a-z0-9-]+-[a-f0-9]+-assetbundle\.sekai\.colorfulpalette\.org", parsed.hostname or "")):
        raise JPError("descriptor assetBaseUrl is not a source-bound official JP endpoint")
    if source.get("profile") and source.get("assetbundleHostHash"):
        expected_host = f"{source['profile']}-{source['assetbundleHostHash']}-assetbundle.sekai.colorfulpalette.org"
        if parsed.hostname != expected_host:
            raise JPError("descriptor source identity disagrees with its bundle host")
    if not isinstance(data.get("bundles"), dict) or not data["bundles"]:
        raise JPError("descriptor contains no bundle index")
    # Reuse canonical path, duplicate and dependency-name validation before
    # any client is created or private cookie is attached to a request.
    Manifest(data["bundles"], data.get("version"))
    return data


def plan(path, *, families=(), include=()):
    descriptor = load_descriptor(path)
    manifest = Manifest(descriptor["bundles"], descriptor.get("version"))
    if include:
        unknown = sorted(set(include) - set(manifest.entries))
        if unknown:
            raise JPError(f"requested roots are absent from the manifest: {unknown}")
        roots = set(include)
    else:
        roots = set(manifest.roots())
        roots.update(name for name, entry in manifest.entries.items()
                     if name.startswith("mysekai/") and not entry.in_player_build)
        # MYSEKAI's actor gate uses scenario part voices for Virtual Singers.
        # They are runtime inputs even though their packages live outside the
        # mysekai/ prefix (the older sound/scenario/part_voice route is unused).
        roots.update(name for name, entry in manifest.entries.items()
                     if name.startswith("sound/scenario/voice/part_voice_")
                     and not entry.in_player_build)
    if families:
        from .assets.router import route
        allowed = set(families)
        roots = {name for name in roots if ("mysekai" in allowed and name.startswith("mysekai/"))
                 or (route(flatten(name)) and route(flatten(name)).domain in allowed)}
    roots = sorted(roots)
    required = manifest.required_bundles(roots)
    entries = [entry for entry in required if not entry.in_player_build]
    builtin = [entry.bundle_name for entry in required if entry.in_player_build]
    return {"version": 1, "source": descriptor["source"], "roots": roots,
            "bundles": [entry.bundle_name for entry in entries], "builtinRequirements": builtin,
            "summary": {"roots": len(roots), "dependencyClosedBundles": len(entries),
                        "builtinRequirements": len(builtin),
                        "declaredBytes": sum(entry.file_size for entry in entries)}}


def pull_descriptor(path, *, out=None, extract_out=None, workers=4, retries=4,
                    master=None, master_cache=None, include=()):
    descriptor = load_descriptor(path)
    source = descriptor["source"]
    identity = f"{source['assetVersion']}-{source['assetHash'][:12]}"
    workspace = Path(out) if out else Path.cwd() / "moly-pull-output" / "jp" / identity
    extracted = Path(extract_out) if extract_out else Path.cwd() / "local-data-jp"
    from .fetch import pull
    selection = plan(path, include=include)
    client = Client(source["appVersion"], source.get("appHash", descriptor.get("appHash", "public")))
    client.issue_signature()
    report = pull(path, workspace, descriptor["assetBaseUrl"], workers, retries,
                  master=master, master_cache=master_cache, extract_out=extracted,
                  fixture_meshes=True, fixture_particles=True,
                  roots=selection["roots"],
                  download_headers={"Cookie": client.cookie})
    provenance = public_source_document({
        "version": 1, "kind": "moly-source", "source": source,
        "descriptorSha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    })
    write_json(extracted / "source.json", provenance, mode=PUBLIC_FILE_MODE)
    return {"source": source, "plan": selection, **report}
