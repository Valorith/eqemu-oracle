from __future__ import annotations

import base64
import ctypes
import ftplib
import getpass
import hashlib
import os
import platform
import posixpath
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .constants import PLUGIN_ROOT
from .utils import dump_json, ensure_dir, load_json, short_hash


PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PROTOCOL_CHOICES = ("ftps", "ftp")
DEFAULT_MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
WRITE_HISTORY_LIMIT = 25
WRITE_HISTORY_MAX_BYTES = 250 * 1024 * 1024
APP_ENV_ROOT = "EQEMU_ORACLE_HOME"
EQEMU_REMOTE_TOP_LEVEL_DIRS = ("binaries", "logs", "plugins", "quests")
EQEMU_REMOTE_KNOWN_DIRS = {
    "binaries": "Binary package folders",
    "logs": "Server logs",
    "logs/crashes": "World/server crash reports",
    "logs/zone": "Zone crash reports",
    "plugins": "Global Perl plugin scripts",
    "quests": "Zone and global quest scripts",
    "quests/global": "Global quest scripts",
    "quests/global/items": "Global item scripts",
    "quests/global/spells": "Global spell scripts",
}
EQEMU_REMOTE_MAP_SCOPES = ("auto", "overview", "quests", "zone", "plugins", "logs", "binaries", "global", "global-items", "global-spells")


class RemoteConfigError(ValueError):
    pass


class RemoteSecretError(RuntimeError):
    pass


class RemoteTransferError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteProfile:
    name: str
    protocol: str
    host: str
    port: int
    username: str
    root_path: str = "/"
    passive: bool = True
    verify_tls: bool = True
    allow_insecure: bool = False
    read_only: bool = False
    tls_cert_sha256: str | None = None
    secret_ref: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RemoteProfile":
        return cls(
            name=str(payload.get("name", "")),
            protocol=str(payload.get("protocol", "")),
            host=str(payload.get("host", "")),
            port=int(payload.get("port", 0)),
            username=str(payload.get("username", "")),
            root_path=str(payload.get("root_path", "/")),
            passive=bool(payload.get("passive", True)),
            verify_tls=bool(payload.get("verify_tls", True)),
            allow_insecure=bool(payload.get("allow_insecure", False)),
            read_only=bool(payload.get("read_only", False)),
            tls_cert_sha256=payload.get("tls_cert_sha256") if isinstance(payload.get("tls_cert_sha256"), str) else None,
            secret_ref=payload.get("secret_ref") if isinstance(payload.get("secret_ref"), dict) else None,
            created_at=payload.get("created_at") if isinstance(payload.get("created_at"), str) else None,
            updated_at=payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "protocol": self.protocol,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "root_path": self.root_path,
            "passive": self.passive,
            "verify_tls": self.verify_tls,
            "allow_insecure": self.allow_insecure,
            "read_only": self.read_only,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.tls_cert_sha256 is not None:
            payload["tls_cert_sha256"] = self.tls_cert_sha256
        if self.secret_ref is not None:
            payload["secret_ref"] = self.secret_ref
        return payload


class SecretStore(Protocol):
    def store_password(self, profile: RemoteProfile, password: str) -> dict[str, Any]:
        ...

    def load_password(self, profile: RemoteProfile) -> str:
        ...

    def delete_password(self, profile: RemoteProfile) -> None:
        ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def user_data_root() -> Path:
    override = os.environ.get(APP_ENV_ROOT)
    if override:
        return Path(override).expanduser()
    system = platform.system().lower()
    if system == "windows":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "EQEmu Oracle"
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "EQEmu Oracle"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "eqemu-oracle"


def profiles_path(root: Path | None = None) -> Path:
    return (root or user_data_root()) / "server-connections.json"


def staging_root(root: Path | None = None) -> Path:
    return (root or user_data_root()) / "staged-files"


def backup_root(root: Path | None = None) -> Path:
    return (root or user_data_root()) / "remote-backups"


def _load_profiles(path: Path | None = None) -> dict[str, Any]:
    target = path or profiles_path()
    if not target.exists():
        return {"version": 1, "profiles": {}}
    payload = load_json(target)
    if not isinstance(payload, dict):
        raise RemoteConfigError(f"Invalid remote profile store at {target}")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        payload["profiles"] = {}
    return payload


def _write_profiles(payload: dict[str, Any], path: Path | None = None) -> None:
    target = path or profiles_path()
    dump_json(target, payload)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def _normalize_profile_name(value: str) -> str:
    name = value.strip()
    if not PROFILE_NAME_RE.fullmatch(name):
        raise RemoteConfigError("Profile names must be 1-64 characters and use only letters, numbers, dot, underscore, or dash.")
    return name


def _normalize_root_path(value: str | None) -> str:
    root = (value or "/").strip().replace("\\", "/")
    if not root:
        return "/"
    if not root.startswith("/"):
        root = f"/{root}"
    normalized = posixpath.normpath(root)
    return "/" if normalized in {"", "."} else normalized


def _validate_protocol(protocol: str, *, allow_insecure: bool) -> str:
    normalized = protocol.strip().lower()
    if normalized not in PROTOCOL_CHOICES:
        raise RemoteConfigError(f"Unsupported protocol '{protocol}'. Expected one of: {', '.join(PROTOCOL_CHOICES)}.")
    if normalized == "ftp" and not allow_insecure:
        raise RemoteConfigError("Plain FTP sends credentials and file data without encryption. Set allow_insecure=true to make that risk explicit.")
    return normalized


def _normalize_sha256_fingerprint(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Fa-f0-9]", "", value).upper()
    if not cleaned:
        return None
    if len(cleaned) != 64 or not re.fullmatch(r"[A-F0-9]{64}", cleaned):
        raise RemoteConfigError("TLS certificate fingerprint must be a SHA-256 hex value.")
    return cleaned


def _normalize_sha256_hex(value: str | None, *, field_name: str = "sha256") -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Fa-f0-9]", "", value).lower()
    if len(cleaned) != 64 or not re.fullmatch(r"[a-f0-9]{64}", cleaned):
        raise RemoteConfigError(f"{field_name} must be a SHA-256 hex value.")
    return cleaned


def build_profile(
    *,
    name: str,
    protocol: str = "ftps",
    host: str,
    port: int | None = None,
    username: str,
    root_path: str = "/",
    passive: bool = True,
    verify_tls: bool = True,
    allow_insecure: bool = False,
    read_only: bool | None = None,
    tls_cert_sha256: str | None = None,
    existing: RemoteProfile | None = None,
) -> RemoteProfile:
    profile_name = _normalize_profile_name(name)
    resolved_protocol = _validate_protocol(protocol, allow_insecure=allow_insecure)
    resolved_host = host.strip()
    if not resolved_host or any(char.isspace() for char in resolved_host):
        raise RemoteConfigError("Host must be a non-empty hostname or IP address without whitespace.")
    resolved_username = username.strip()
    if not resolved_username:
        raise RemoteConfigError("Username must be non-empty.")
    resolved_port = int(port if port is not None else 21)
    if resolved_port < 1 or resolved_port > 65535:
        raise RemoteConfigError("Port must be between 1 and 65535.")
    resolved_read_only = existing.read_only if read_only is None and existing is not None else bool(read_only)
    resolved_tls_cert_sha256 = (
        existing.tls_cert_sha256
        if tls_cert_sha256 is None and existing is not None
        else _normalize_sha256_fingerprint(tls_cert_sha256)
    )
    now = _utc_now()
    return RemoteProfile(
        name=profile_name,
        protocol=resolved_protocol,
        host=resolved_host,
        port=resolved_port,
        username=resolved_username,
        root_path=_normalize_root_path(root_path),
        passive=passive,
        verify_tls=verify_tls,
        allow_insecure=allow_insecure,
        read_only=resolved_read_only,
        tls_cert_sha256=resolved_tls_cert_sha256,
        secret_ref=existing.secret_ref if existing is not None else None,
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
    )


def _secret_service_name(profile: RemoteProfile) -> str:
    return f"eqemu-oracle.ftp.{profile.name}"


def _secret_account_name(profile: RemoteProfile) -> str:
    return f"{profile.name}:{profile.username}"


class SystemSecretStore:
    def store_password(self, profile: RemoteProfile, password: str) -> dict[str, Any]:
        if not password:
            raise RemoteSecretError("Password must be non-empty.")
        system = platform.system().lower()
        if system == "windows":
            return {"backend": "windows-dpapi", "blob": _windows_dpapi_protect(password)}
        if system == "darwin":
            service = _secret_service_name(profile)
            account = _secret_account_name(profile)
            _run_keychain(["add-generic-password", "-a", account, "-s", service, "-w", password, "-U"])
            return {"backend": "macos-keychain", "service": service, "account": account}
        if shutil.which("secret-tool"):
            attributes = ["application", "eqemu-oracle", "profile", profile.name, "username", profile.username]
            completed = subprocess.run(
                ["secret-tool", "store", "--label", f"EQEmu Oracle FTP profile {profile.name}", *attributes],
                input=password,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise RemoteSecretError(completed.stderr.strip() or "Unable to store password with Secret Service.")
            return {"backend": "secret-service", "attributes": attributes}
        raise RemoteSecretError(
            "No supported secure credential store was found. On Linux, install `secret-tool`/libsecret; on Windows and macOS the built-in credential stores are used."
        )

    def load_password(self, profile: RemoteProfile) -> str:
        secret_ref = profile.secret_ref or {}
        backend = secret_ref.get("backend")
        if backend == "windows-dpapi":
            blob = secret_ref.get("blob")
            if not isinstance(blob, str) or not blob:
                raise RemoteSecretError("Stored Windows credential metadata is missing its encrypted blob.")
            return _windows_dpapi_unprotect(blob)
        if backend == "macos-keychain":
            service = secret_ref.get("service")
            account = secret_ref.get("account")
            if not isinstance(service, str) or not isinstance(account, str):
                raise RemoteSecretError("Stored macOS Keychain credential metadata is incomplete.")
            return _run_keychain(["find-generic-password", "-a", account, "-s", service, "-w"]).strip()
        if backend == "secret-service":
            attributes = secret_ref.get("attributes")
            if not isinstance(attributes, list) or not all(isinstance(item, str) for item in attributes):
                raise RemoteSecretError("Stored Secret Service credential metadata is incomplete.")
            completed = subprocess.run(["secret-tool", "lookup", *attributes], capture_output=True, text=True, check=False)
            if completed.returncode != 0 or not completed.stdout:
                raise RemoteSecretError("Unable to read password from Secret Service.")
            return completed.stdout.rstrip("\n")
        raise RemoteSecretError("Remote profile does not have a supported stored credential reference.")

    def delete_password(self, profile: RemoteProfile) -> None:
        secret_ref = profile.secret_ref or {}
        backend = secret_ref.get("backend")
        if backend == "macos-keychain":
            service = secret_ref.get("service")
            account = secret_ref.get("account")
            if isinstance(service, str) and isinstance(account, str):
                _run_keychain(["delete-generic-password", "-a", account, "-s", service], allow_failure=True)
            return
        if backend == "secret-service":
            attributes = secret_ref.get("attributes")
            if isinstance(attributes, list) and all(isinstance(item, str) for item in attributes):
                subprocess.run(["secret-tool", "clear", *attributes], capture_output=True, text=True, check=False)


def _run_keychain(args: list[str], *, allow_failure: bool = False) -> str:
    completed = subprocess.run(["security", *args], capture_output=True, text=True, check=False)
    if completed.returncode != 0 and not allow_failure:
        raise RemoteSecretError(completed.stderr.strip() or "macOS Keychain command failed.")
    return completed.stdout


def _windows_dpapi_protect(password: str) -> str:
    data = password.encode("utf-8")
    encrypted = _windows_crypt_protect(data)
    return base64.b64encode(encrypted).decode("ascii")


def _windows_dpapi_unprotect(blob: str) -> str:
    encrypted = base64.b64decode(blob.encode("ascii"))
    return _windows_crypt_unprotect(encrypted).decode("utf-8")


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob_from_bytes(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _windows_crypt_protect(data: bytes) -> bytes:
    if platform.system().lower() != "windows":
        raise RemoteSecretError("Windows DPAPI is only available on Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _buffer = _blob_from_bytes(data)
    out_blob = _DataBlob()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise RemoteSecretError("Windows DPAPI failed to protect the password.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _windows_crypt_unprotect(data: bytes) -> bytes:
    if platform.system().lower() != "windows":
        raise RemoteSecretError("Windows DPAPI is only available on Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _buffer = _blob_from_bytes(data)
    out_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise RemoteSecretError("Windows DPAPI failed to read the stored password.")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def public_profile(profile: RemoteProfile) -> dict[str, Any]:
    secret_ref = profile.secret_ref or {}
    tls_verification_mode = "pinned-certificate" if profile.tls_cert_sha256 else ("standard" if profile.verify_tls else "disabled")
    return {
        "name": profile.name,
        "protocol": profile.protocol,
        "host": profile.host,
        "port": profile.port,
        "username": profile.username,
        "root_path": profile.root_path,
        "passive": profile.passive,
        "verify_tls": profile.verify_tls,
        "tls_verification_mode": tls_verification_mode,
        "tls_cert_sha256": profile.tls_cert_sha256,
        "allow_insecure": profile.allow_insecure,
        "read_only": profile.read_only,
        "credential_backend": secret_ref.get("backend"),
        "has_stored_password": bool(secret_ref),
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def save_profile(
    profile: RemoteProfile,
    password: str,
    *,
    overwrite: bool = False,
    config_path: Path | None = None,
    secret_store: SecretStore | None = None,
) -> dict[str, Any]:
    payload = _load_profiles(config_path)
    profiles = payload["profiles"]
    existing_payload = profiles.get(profile.name)
    if existing_payload is not None and not overwrite:
        raise RemoteConfigError(f"Remote profile '{profile.name}' already exists. Pass overwrite=true to replace it.")
    existing_profile = RemoteProfile.from_dict(existing_payload) if isinstance(existing_payload, dict) else None
    final_profile = build_profile(
        name=profile.name,
        protocol=profile.protocol,
        host=profile.host,
        port=profile.port,
        username=profile.username,
        root_path=profile.root_path,
        passive=profile.passive,
        verify_tls=profile.verify_tls,
        allow_insecure=profile.allow_insecure,
        read_only=existing_profile.read_only if existing_profile is not None else profile.read_only,
        tls_cert_sha256=profile.tls_cert_sha256,
        existing=existing_profile,
    )
    store = secret_store or SystemSecretStore()
    if existing_profile is not None and existing_profile.secret_ref:
        store.delete_password(existing_profile)
    secret_ref = store.store_password(final_profile, password)
    final_profile = RemoteProfile(**{**final_profile.to_dict(), "secret_ref": secret_ref})
    profiles[final_profile.name] = final_profile.to_dict()
    payload["version"] = 1
    _write_profiles(payload, config_path)
    return {
        "saved": True,
        "profile": public_profile(final_profile),
        "profiles_path": str((config_path or profiles_path()).resolve()),
    }


def remove_profile(
    name: str,
    *,
    config_path: Path | None = None,
    secret_store: SecretStore | None = None,
) -> dict[str, Any]:
    profile_name = _normalize_profile_name(name)
    payload = _load_profiles(config_path)
    profiles = payload["profiles"]
    existing_payload = profiles.pop(profile_name, None)
    if existing_payload is None:
        raise RemoteConfigError(f"Remote profile '{profile_name}' does not exist.")
    profile = RemoteProfile.from_dict(existing_payload)
    (secret_store or SystemSecretStore()).delete_password(profile)
    _write_profiles(payload, config_path)
    return {"removed": True, "profile": profile_name}


def load_profile(name: str, *, config_path: Path | None = None) -> RemoteProfile:
    profile_name = _normalize_profile_name(name)
    payload = _load_profiles(config_path)
    profile_payload = payload["profiles"].get(profile_name)
    if not isinstance(profile_payload, dict):
        raise RemoteConfigError(f"Remote profile '{profile_name}' does not exist. Run the FTP onboarding setup first.")
    profile = RemoteProfile.from_dict(profile_payload)
    _validate_protocol(profile.protocol, allow_insecure=profile.allow_insecure)
    return profile


def list_profiles(*, config_path: Path | None = None) -> dict[str, Any]:
    payload = _load_profiles(config_path)
    profiles = [
        public_profile(RemoteProfile.from_dict(profile_payload))
        for profile_payload in payload["profiles"].values()
        if isinstance(profile_payload, dict)
    ]
    profiles.sort(key=lambda item: str(item["name"]))
    return {
        "profiles": profiles,
        "count": len(profiles),
        "profiles_path": str((config_path or profiles_path()).resolve()),
        "presentation": {
            "markdown": (
                "No EQEmu server FTP profiles are configured yet."
                if not profiles
                else "\n".join(
                    f"- `{item['name']}` {item['protocol']}://{item['host']}:{item['port']} root `{item['root_path']}` mode `{'read-only' if item['read_only'] else 'read-write'}`"
                    for item in profiles
                )
            )
        },
    }


def set_profile_read_only_mode(
    name: str,
    *,
    read_only: bool,
    confirm_mode_change: bool = False,
    confirm_profile: str | None = None,
    confirm_read_only_mode: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    profile_name = _normalize_profile_name(name)
    target_mode = "read-only" if read_only else "read-write"
    payload = _load_profiles(config_path)
    profiles = payload["profiles"]
    profile_payload = profiles.get(profile_name)
    if not isinstance(profile_payload, dict):
        raise RemoteConfigError(f"Remote profile '{profile_name}' does not exist. Run the FTP onboarding setup first.")
    existing_profile = RemoteProfile.from_dict(profile_payload)
    if not confirm_mode_change:
        return {
            "changed": False,
            "requires_confirmation": True,
            "profile": public_profile(existing_profile),
            "current_read_only": existing_profile.read_only,
            "target_read_only": read_only,
            "target_mode": target_mode,
            "message": "Changing an FTP profile mode requires explicit user instruction and exact confirmation fields.",
            "confirmation_arguments": {
                "profile": profile_name,
                "read_only": read_only,
                "confirm_mode_change": True,
                "confirm_profile": profile_name,
                "confirm_read_only_mode": target_mode,
            },
            "presentation": {
                "markdown": (
                    f"FTP profile `{profile_name}` mode change preview: target mode `{target_mode}`. "
                    "Only run the confirmed call after explicit user instruction."
                )
            },
        }
    if confirm_profile != profile_name:
        raise RemoteConfigError("confirm_profile must exactly match the profile name for an FTP mode change.")
    if confirm_read_only_mode != target_mode:
        raise RemoteConfigError("confirm_read_only_mode must exactly match the requested FTP mode.")
    updated_profile = RemoteProfile(**{**existing_profile.to_dict(), "read_only": read_only, "updated_at": _utc_now()})
    profiles[profile_name] = updated_profile.to_dict()
    payload["version"] = 1
    _write_profiles(payload, config_path)
    return {
        "changed": existing_profile.read_only != read_only,
        "requires_confirmation": False,
        "profile": public_profile(updated_profile),
        "previous_read_only": existing_profile.read_only,
        "read_only": updated_profile.read_only,
        "mode": target_mode,
        "profiles_path": str((config_path or profiles_path()).resolve()),
        "presentation": {"markdown": f"FTP profile `{profile_name}` is now `{target_mode}`."},
    }


def _assert_profile_allows_write(profile: RemoteProfile, action: str) -> None:
    if profile.read_only:
        raise RemoteConfigError(
            f"FTP profile '{profile.name}' is in read-only mode. Refusing {action}; only read, list, test, stage, history, and preview operations are allowed."
        )


def _resolve_remote_path(profile: RemoteProfile, remote_path: str | None) -> str:
    requested = (remote_path or ".").strip().replace("\\", "/")
    if "\x00" in requested:
        raise RemoteConfigError("Remote paths must not contain NUL bytes.")
    if requested.startswith("/"):
        candidate = posixpath.normpath(requested)
    else:
        candidate = posixpath.normpath(posixpath.join(profile.root_path, requested))
    if candidate in {"", "."}:
        candidate = profile.root_path
    root = profile.root_path
    if root != "/" and not (candidate == root or candidate.startswith(f"{root.rstrip('/')}/")):
        raise RemoteConfigError(f"Remote path '{remote_path}' escapes configured root '{root}'.")
    return candidate


def _relative_remote_path(profile: RemoteProfile, remote_path: str) -> str:
    if profile.root_path == "/":
        return remote_path.lstrip("/")
    relative = posixpath.relpath(remote_path, profile.root_path)
    return "" if relative == "." else relative


def _relative_remote_parts(profile: RemoteProfile, remote_path: str) -> list[str]:
    relative = _relative_remote_path(profile, remote_path).strip("/")
    return [part for part in relative.split("/") if part]


def _relative_remote_depth(profile: RemoteProfile, remote_path: str, base_path: str) -> int:
    relative = posixpath.relpath(remote_path, base_path)
    if relative in {"", "."}:
        return 0
    return len([part for part in relative.split("/") if part])


def _eqemu_path_role(profile: RemoteProfile, path: str, entry_type: str | None = None) -> dict[str, Any]:
    parts = _relative_remote_parts(profile, path)
    name = parts[-1] if parts else posixpath.basename(path.rstrip("/")) or path
    suffix = Path(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix else name
    metadata: dict[str, Any] = {
        "relative_path": "/".join(parts),
        "role": "server_root" if not parts else "unknown",
        "description": "Configured EQEmu server FTP root" if not parts else "Unclassified remote path",
        "category": "root" if not parts else "unknown",
    }
    if len(parts) == 1 and parts[0] in EQEMU_REMOTE_TOP_LEVEL_DIRS:
        metadata["role"] = f"{parts[0]}_root"
        metadata["category"] = parts[0]
        metadata["description"] = EQEMU_REMOTE_KNOWN_DIRS.get(parts[0], metadata["description"])
    elif parts[:1] == ["binaries"]:
        metadata["role"] = "binary_package_dir" if entry_type == "dir" else "binary_package_file"
        metadata["category"] = "binaries"
        metadata["description"] = "Binary package folder or file"
    elif parts == ["logs", "crashes"]:
        metadata["role"] = "server_crash_reports_root"
        metadata["category"] = "logs"
        metadata["description"] = EQEMU_REMOTE_KNOWN_DIRS["logs/crashes"]
    elif parts[:2] == ["logs", "crashes"]:
        metadata["role"] = "server_crash_report" if suffix == ".txt" else "server_crash_artifact"
        metadata["category"] = "logs"
        metadata["description"] = "Server crash report text file" if suffix == ".txt" else "Server crash report artifact"
    elif parts == ["logs", "zone"]:
        metadata["role"] = "zone_crash_reports_root"
        metadata["category"] = "logs"
        metadata["description"] = EQEMU_REMOTE_KNOWN_DIRS["logs/zone"]
    elif parts[:2] == ["logs", "zone"]:
        metadata["role"] = "zone_crash_report" if suffix == ".txt" else "zone_log_artifact"
        metadata["category"] = "logs"
        metadata["description"] = "Zone crash report text file" if suffix == ".txt" else "Zone log artifact"
    elif parts[:1] == ["logs"]:
        metadata["category"] = "logs"
        if entry_type == "dir":
            metadata["role"] = "logs_support_dir"
            metadata["description"] = "Log support path"
        elif suffix == ".log":
            metadata["role"] = "server_log_file"
            metadata["description"] = "Server runtime log file"
        else:
            metadata["role"] = "server_log_artifact"
            metadata["description"] = "Server log artifact"
    elif parts == ["plugins"]:
        metadata["role"] = "plugins_root"
        metadata["category"] = "plugins"
        metadata["description"] = EQEMU_REMOTE_KNOWN_DIRS["plugins"]
    elif parts[:1] == ["plugins"]:
        metadata["category"] = "plugins"
        if suffix == ".pl":
            metadata["role"] = "perl_plugin_script"
            metadata["description"] = "Top-level EQEmu Perl plugin script"
            metadata["script_language"] = "perl"
            metadata["script_key"] = stem.lower()
        else:
            metadata["role"] = "plugin_support_file" if entry_type != "dir" else "plugin_support_dir"
            metadata["description"] = "Plugin support path"
    elif parts == ["quests"]:
        metadata["role"] = "quests_root"
        metadata["category"] = "quests"
        metadata["description"] = EQEMU_REMOTE_KNOWN_DIRS["quests"]
    elif parts == ["quests", "global"]:
        metadata["role"] = "global_quests_root"
        metadata["category"] = "quests"
        metadata["description"] = EQEMU_REMOTE_KNOWN_DIRS["quests/global"]
        metadata["scope"] = "global"
    elif parts == ["quests", "global", "items"]:
        metadata["role"] = "global_item_scripts_root"
        metadata["category"] = "quests"
        metadata["description"] = EQEMU_REMOTE_KNOWN_DIRS["quests/global/items"]
        metadata["scope"] = "global_items"
    elif parts == ["quests", "global", "spells"]:
        metadata["role"] = "global_spell_scripts_root"
        metadata["category"] = "quests"
        metadata["description"] = EQEMU_REMOTE_KNOWN_DIRS["quests/global/spells"]
        metadata["scope"] = "global_spells"
    elif parts[:1] == ["quests"]:
        metadata["category"] = "quests"
        zone = parts[1] if len(parts) >= 2 else None
        if len(parts) == 2 and entry_type == "dir":
            if isinstance(zone, str) and zone.startswith("."):
                metadata["role"] = "quest_support_dir"
                metadata["description"] = "Quest support path"
            else:
                metadata["role"] = "quest_zone_dir" if zone != "global" else "global_quests_root"
                metadata["description"] = "Zone-specific quest folder" if zone != "global" else EQEMU_REMOTE_KNOWN_DIRS["quests/global"]
                metadata["zone"] = zone
                metadata["scope"] = "zone" if zone != "global" else "global"
        elif suffix in {".pl", ".lua"}:
            metadata["role"] = "quest_script"
            metadata["description"] = "EQEmu quest script"
            metadata["script_language"] = "lua" if suffix == ".lua" else "perl"
            metadata["script_key"] = stem.lower()
            if len(parts) >= 4 and parts[:3] == ["quests", "global", "items"]:
                metadata["scope"] = "global_items"
                metadata["script_target"] = "item"
            elif len(parts) >= 4 and parts[:3] == ["quests", "global", "spells"]:
                metadata["scope"] = "global_spells"
                metadata["script_target"] = "spell"
            elif len(parts) >= 3 and parts[1] == "global":
                metadata["scope"] = "global"
            elif len(parts) >= 3:
                metadata["scope"] = "zone"
                metadata["zone"] = parts[1]
            if stem.isdigit():
                metadata["script_target"] = metadata.get("script_target", "npc_type_id")
            elif metadata.get("script_target") not in {"item", "spell"}:
                metadata["script_target"] = "npc_name_or_global"
        else:
            metadata["role"] = "quest_support_dir" if entry_type == "dir" else "quest_support_file"
            metadata["description"] = "Quest support path"
            if len(parts) >= 2 and not parts[1].startswith("."):
                metadata["zone"] = parts[1] if parts[1] != "global" else None
    return metadata


def _annotate_eqemu_entry(profile: RemoteProfile, entry: dict[str, Any]) -> dict[str, Any]:
    annotated = dict(entry)
    path = str(annotated.get("path", ""))
    entry_type = annotated.get("type")
    role = _eqemu_path_role(profile, path, entry_type if isinstance(entry_type, str) else None)
    annotated.update(role)
    return annotated


def _script_parent_key(profile: RemoteProfile, path: str) -> str:
    relative = _relative_remote_path(profile, path).strip("/")
    return posixpath.dirname(relative)


def _detect_script_priority_conflicts(profile: RemoteProfile, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    for entry in entries:
        language = entry.get("script_language")
        script_key = entry.get("script_key")
        if language not in {"perl", "lua"} or not isinstance(script_key, str):
            continue
        category = str(entry.get("category", "unknown"))
        parent_key = _script_parent_key(profile, str(entry.get("path", "")))
        group = grouped.setdefault((category, parent_key, script_key), {})
        group[str(language)] = entry
    conflicts: list[dict[str, Any]] = []
    for (category, parent_key, script_key), languages in sorted(grouped.items()):
        lua_entry = languages.get("lua")
        perl_entry = languages.get("perl")
        if lua_entry is None or perl_entry is None:
            continue
        conflicts.append(
            {
                "category": category,
                "directory": parent_key,
                "script_key": script_key,
                "active_language": "lua",
                "shadowed_language": "perl",
                "active_path": lua_entry.get("path"),
                "shadowed_path": perl_entry.get("path"),
                "note": "When .lua and .pl scripts share the same base name in the same EQEmu script directory, the Lua script takes priority and the Perl script is not loaded.",
            }
        )
    return conflicts


def _map_scope_target(scope: str, remote_path: str | None, zone: str | None) -> tuple[str, int]:
    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope not in EQEMU_REMOTE_MAP_SCOPES:
        raise RemoteConfigError(f"Unsupported map scope '{scope}'. Expected one of: {', '.join(EQEMU_REMOTE_MAP_SCOPES)}.")
    if remote_path:
        return remote_path, 3
    if normalized_scope in {"auto", "overview"}:
        return ".", 2
    if normalized_scope == "quests":
        return "quests", 1
    if normalized_scope == "zone":
        zone_name = (zone or "").strip().strip("/")
        if not zone_name or "/" in zone_name or "\\" in zone_name:
            raise RemoteConfigError("Map scope 'zone' requires a single zone name.")
        return f"quests/{zone_name}", 1
    if normalized_scope == "plugins":
        return "plugins", 1
    if normalized_scope == "logs":
        return "logs", 2
    if normalized_scope == "binaries":
        return "binaries", 1
    if normalized_scope == "global":
        return "quests/global", 2
    if normalized_scope == "global-items":
        return "quests/global/items", 1
    if normalized_scope == "global-spells":
        return "quests/global/spells", 1
    return ".", 2


def _available_file_bucket(entry: dict[str, Any]) -> str | None:
    role = entry.get("role")
    scope = entry.get("scope")
    if role == "quest_script":
        if scope == "global_items":
            return "global_item_scripts"
        if scope == "global_spells":
            return "global_spell_scripts"
        if scope == "global":
            return "global_quest_scripts"
        return "zone_quest_scripts"
    if role == "perl_plugin_script":
        return "plugin_scripts"
    if role == "server_crash_report":
        return "server_crash_reports"
    if role == "zone_crash_report":
        return "zone_crash_reports"
    if role == "server_log_file":
        return "server_logs"
    if role == "zone_log_artifact":
        return "zone_logs"
    if role in {"binary_package_file", "binary_package_dir"}:
        return "binary_packages"
    return None


def _available_file_inventory(entries: list[dict[str, Any]], *, sample_limit: int = 25) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for entry in entries:
        bucket = _available_file_bucket(entry)
        if bucket is None:
            continue
        info = inventory.setdefault(bucket, {"count": 0, "sample_paths": []})
        info["count"] += 1
        samples = info["sample_paths"]
        if isinstance(samples, list) and len(samples) < sample_limit:
            samples.append(entry.get("path"))
    return dict(sorted(inventory.items()))


def _map_followups(remote_path: str, scope: str, zone: str | None, truncated: bool, *, explicit_path: bool) -> list[dict[str, Any]]:
    normalized_scope = (scope or "auto").strip().lower()
    followups: list[dict[str, Any]] = []
    if normalized_scope in {"auto", "overview"} and not explicit_path:
        followups.extend(
            [
                {"scope": "plugins", "reason": "List top-level Perl plugin scripts before plugin work."},
                {"scope": "quests", "reason": "List zone folders and quest scripts before broad quest work."},
                {"scope": "logs", "reason": "List server and zone crash reports before crash triage."},
                {"scope": "binaries", "reason": "List binary package folders before binary/package work."},
            ]
        )
    elif normalized_scope == "quests":
        followups.append({"scope": "zone", "zone": "<zone_short_name>", "reason": "Map a single zone before staging or editing zone-specific scripts."})
        followups.append({"scope": "global", "reason": "Map global quest, item, and spell scripts before changing global behavior."})
    elif normalized_scope == "global":
        followups.append({"scope": "global-items", "reason": "Map global item scripts only."})
        followups.append({"scope": "global-spells", "reason": "Map global spell scripts only."})
    elif normalized_scope == "zone" and zone:
        followups.append({"remote_path": f"quests/{zone}", "reason": "Stage exact active zone script paths from this map before editing."})
    if truncated:
        followups.append({"remote_path": remote_path, "reason": "The map reached its limit; increase `limit`, narrow `scope`, or map a more specific path."})
    return followups


def _summarize_eqemu_remote_layout(profile: RemoteProfile, entries: list[dict[str, Any]], *, include_expected_missing: bool) -> dict[str, Any]:
    present_dirs: set[str] = set()
    counts_by_role: dict[str, int] = {}
    counts_by_category: dict[str, int] = {}
    zones: set[str] = set()
    global_subdirs: set[str] = set()
    scripts = {"lua": 0, "perl": 0}
    crash_reports = {"server": 0, "zone": 0}
    for entry in entries:
        relative_path = str(entry.get("relative_path", "")).strip("/")
        if entry.get("type") == "dir" and relative_path:
            present_dirs.add(relative_path)
        role = str(entry.get("role", "unknown"))
        counts_by_role[role] = counts_by_role.get(role, 0) + 1
        category = str(entry.get("category", "unknown"))
        counts_by_category[category] = counts_by_category.get(category, 0) + 1
        zone = entry.get("zone")
        if isinstance(zone, str) and zone:
            zones.add(zone)
        if relative_path in {"quests/global/items", "quests/global/spells"}:
            global_subdirs.add(relative_path.rsplit("/", 1)[-1])
        language = entry.get("script_language")
        if language in scripts:
            scripts[language] += 1
        if role == "server_crash_report":
            crash_reports["server"] += 1
        elif role == "zone_crash_report":
            crash_reports["zone"] += 1
    top_level_present = sorted(path for path in present_dirs if "/" not in path and path in EQEMU_REMOTE_TOP_LEVEL_DIRS)
    expected_missing = []
    if include_expected_missing:
        expected_missing = [
            {"path": path, "description": description}
            for path, description in EQEMU_REMOTE_KNOWN_DIRS.items()
            if path not in present_dirs
        ]
    return {
        "top_level_present": top_level_present,
        "expected_missing": expected_missing,
        "counts_by_category": dict(sorted(counts_by_category.items())),
        "counts_by_role": dict(sorted(counts_by_role.items())),
        "zone_count": len(zones),
        "sample_zones": sorted(zones)[:25],
        "global_subdirs_present": sorted(global_subdirs),
        "scripts": scripts,
        "crash_reports": crash_reports,
        "layout_notes": [
            "Expected EQEmu roots: binaries, logs, plugins, quests.",
            "logs/crashes contains server crash report text files; logs/zone contains zone crash report text files and may contain zone runtime logs.",
            "logs/*.log may contain top-level server runtime logs.",
            "plugins is expected to contain top-level Perl plugin scripts.",
            "quests contains one folder per zone plus quests/global; quests/global/items and quests/global/spells contain global item and spell scripts.",
            "NPC quest scripts may be named by NPC name or numeric NPC type id.",
            "If the same script base name exists as both .lua and .pl in the same script directory, the Lua script takes priority and the Perl script is not loaded.",
        ],
    }


def _safe_path_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"|?*\x00-\x1f]', "_", value).strip()
    if cleaned in {"", ".", ".."}:
        return "_"
    return cleaned


def _profile_stage_root(profile: RemoteProfile, root: Path | None = None) -> Path:
    return staging_root(root) / _safe_path_component(profile.name)


def _profile_backup_root(profile: RemoteProfile, root: Path | None = None) -> Path:
    return backup_root(root) / _safe_path_component(profile.name)


def _write_history_path(profile: RemoteProfile, root: Path | None = None) -> Path:
    return _profile_backup_root(profile, root) / "write-history.json"


def _new_operation_id(kind: str, profile: RemoteProfile, remote_path: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = short_hash(f"{kind}:{profile.name}:{remote_path}:{uuid.uuid4().hex}", length=8)
    return f"{stamp}-{kind}-{token}"


def _load_write_history(profile: RemoteProfile, root: Path | None = None) -> dict[str, Any]:
    path = _write_history_path(profile, root)
    if not path.exists():
        return {"version": 1, "operations": []}
    payload = load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("operations"), list):
        return {"version": 1, "operations": []}
    return payload


def _write_write_history(profile: RemoteProfile, payload: dict[str, Any], root: Path | None = None) -> None:
    payload["version"] = 1
    dump_json(_write_history_path(profile, root), payload)


def _operation_backup_size(operation: dict[str, Any]) -> int:
    value = operation.get("backup_size")
    return value if isinstance(value, int) and value >= 0 else 0


def _retention_policy_from_history(history: dict[str, Any]) -> dict[str, int]:
    retention = history.get("retention") if isinstance(history.get("retention"), dict) else {}
    max_operations = retention.get("max_operations") if isinstance(retention, dict) else None
    max_bytes = retention.get("max_bytes") if isinstance(retention, dict) else None
    return {
        "max_operations": max_operations if isinstance(max_operations, int) and max_operations >= 1 else WRITE_HISTORY_LIMIT,
        "max_bytes": max_bytes if isinstance(max_bytes, int) and max_bytes >= 1 else WRITE_HISTORY_MAX_BYTES,
    }


def _safe_local_backup_file(profile: RemoteProfile, path_value: Any, root: Path | None = None) -> Path | None:
    if not isinstance(path_value, str) or not path_value:
        return None
    path = Path(path_value)
    profile_root = _profile_backup_root(profile, root)
    try:
        resolved = path.resolve()
        resolved.relative_to(profile_root.resolve())
    except (OSError, ValueError):
        return None
    try:
        if resolved == _write_history_path(profile, root).resolve():
            return None
    except OSError:
        return None
    return resolved


def _remove_empty_backup_parents(path: Path, profile_root: Path) -> None:
    parent = path.parent
    stop = profile_root.resolve()
    while parent.exists():
        try:
            if parent.resolve() == stop or any(parent.iterdir()):
                break
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break


def _remove_local_backup_file(profile: RemoteProfile, path_value: Any, root: Path | None = None) -> bool:
    path = _safe_local_backup_file(profile, path_value, root)
    if path is None or not path.is_file():
        return False
    profile_root = _profile_backup_root(profile, root)
    try:
        path.unlink()
    except OSError:
        return False
    _remove_empty_backup_parents(path, profile_root)
    return True


def _prune_write_history(
    profile: RemoteProfile,
    history: dict[str, Any],
    root: Path | None = None,
    *,
    max_operations: int = WRITE_HISTORY_LIMIT,
    max_bytes: int = WRITE_HISTORY_MAX_BYTES,
    apply: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if max_operations < 1:
        raise RemoteConfigError("max_operations must be at least 1.")
    if max_bytes < 1:
        raise RemoteConfigError("max_bytes must be at least 1.")
    operations = [operation for operation in history.get("operations", []) if isinstance(operation, dict)]
    retained_reversed: list[dict[str, Any]] = []
    pruned_operations: list[dict[str, Any]] = []
    total_bytes = 0
    for operation in reversed(operations):
        size = _operation_backup_size(operation)
        within_count = len(retained_reversed) < max_operations
        within_bytes = total_bytes + size <= max_bytes or not retained_reversed
        if within_count and within_bytes:
            retained_reversed.append(operation)
            total_bytes += size
        else:
            pruned_operations.append(operation)
    backup_paths_to_remove = [
        path
        for path in (_safe_local_backup_file(profile, operation.get("backup_path"), root) for operation in pruned_operations)
        if path is not None and path.is_file()
    ]
    removed_backup_files = 0
    if apply:
        for path in backup_paths_to_remove:
            if _remove_local_backup_file(profile, str(path), root):
                removed_backup_files += 1
    history["operations"] = list(reversed(retained_reversed))
    history["retention"] = {"max_operations": max_operations, "max_bytes": max_bytes}
    return history, {
        "retained_operations": len(history["operations"]),
        "retained_backup_bytes": total_bytes,
        "removed_history_operations": len(pruned_operations) if apply else 0,
        "history_operations_to_remove": len(pruned_operations),
        "removed_backup_files": removed_backup_files,
        "backup_files_to_remove": len(backup_paths_to_remove),
        "pruned_operation_ids": [str(operation.get("id")) for operation in pruned_operations if operation.get("id")],
        "pruned_backup_paths": [str(path) for path in backup_paths_to_remove],
    }


def _append_write_operation(profile: RemoteProfile, operation: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    history = _load_write_history(profile, root)
    operations = [item for item in history.get("operations", []) if isinstance(item, dict)]
    operations.append(operation)
    history["operations"] = operations
    _prune_write_history(profile, history, root)
    _write_write_history(profile, history, root)
    return history


def _operation_backup_paths(profile: RemoteProfile, operations: list[dict[str, Any]], root: Path | None = None) -> set[Path]:
    paths: set[Path] = set()
    for operation in operations:
        path = _safe_local_backup_file(profile, operation.get("backup_path"), root)
        if path is not None:
            paths.add(path)
    return paths


def _orphan_backup_files(profile: RemoteProfile, root: Path | None = None, *, exclude_paths: set[Path] | None = None) -> list[Path]:
    profile_root = _profile_backup_root(profile, root)
    if not profile_root.exists():
        return []
    try:
        resolved_root = profile_root.resolve()
        history_path = _write_history_path(profile, root).resolve()
    except OSError:
        return []
    excluded = {path.resolve() for path in (exclude_paths or set())}
    orphaned: list[Path] = []
    for path in sorted(profile_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
            resolved.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        if resolved == history_path or resolved in excluded:
            continue
        orphaned.append(resolved)
    return orphaned


def garbage_collect_write_history(
    profile_name: str,
    *,
    apply: bool = False,
    confirm_write: bool = False,
    prune_orphans: bool = True,
    max_operations: int = WRITE_HISTORY_LIMIT,
    max_bytes: int = WRITE_HISTORY_MAX_BYTES,
    config_path: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    if max_operations < 1:
        raise RemoteConfigError("max_operations must be at least 1.")
    if max_bytes < 1:
        raise RemoteConfigError("max_bytes must be at least 1.")
    profile = load_profile(profile_name, config_path=config_path)
    if apply:
        _assert_profile_allows_write(profile, "FTP undo garbage collection apply")
    history = _load_write_history(profile, data_root)
    operations = [operation for operation in history.get("operations", []) if isinstance(operation, dict)]
    should_apply = bool(apply and confirm_write)
    working_history = dict(history)
    working_history["operations"] = list(operations)
    pruned_history, prune_stats = _prune_write_history(
        profile,
        working_history,
        data_root,
        max_operations=max_operations,
        max_bytes=max_bytes,
        apply=should_apply,
    )
    retained_operations = [operation for operation in pruned_history.get("operations", []) if isinstance(operation, dict)]
    retained_paths = _operation_backup_paths(profile, retained_operations, data_root)
    pruned_paths = {Path(path).resolve() for path in prune_stats.get("pruned_backup_paths", []) if isinstance(path, str)}
    orphan_candidates = _orphan_backup_files(profile, data_root, exclude_paths=retained_paths | pruned_paths) if prune_orphans else []
    removed_orphan_files = 0
    if should_apply and prune_orphans:
        for path in orphan_candidates:
            if _remove_local_backup_file(profile, str(path), data_root):
                removed_orphan_files += 1
    if should_apply:
        _write_write_history(profile, pruned_history, data_root)

    requires_confirmation = bool(apply and not confirm_write)
    backup_files_to_remove = int(prune_stats["backup_files_to_remove"])
    orphan_files_to_remove = len(orphan_candidates) if prune_orphans else 0
    removed_backup_files = int(prune_stats["removed_backup_files"]) if should_apply else 0
    removed_history_operations = int(prune_stats["removed_history_operations"]) if should_apply else 0
    action = "Applied" if should_apply else "Previewed"
    cleanup_verb = "removed" if should_apply else "would remove"
    lines = [
        f"{action} FTP undo garbage collection for `{profile.name}`.",
        f"- Retention: keep {max_operations} operations or {max_bytes} bytes of restore-point data.",
        f"- Operations before/retained: {len(operations)}/{len(retained_operations)}.",
        f"- History operations beyond retention: {prune_stats['history_operations_to_remove']}.",
        f"- Restore-point files beyond retention {cleanup_verb}: {removed_backup_files if should_apply else backup_files_to_remove}.",
    ]
    if prune_orphans:
        lines.append(f"- Orphan restore-point files {cleanup_verb}: {removed_orphan_files if should_apply else orphan_files_to_remove}.")
    else:
        lines.append("- Orphan cleanup skipped.")
    if requires_confirmation:
        lines.append("Re-run with `apply=true` and `confirm_write=true` to delete these local restore-point files.")

    return {
        "applied": should_apply,
        "requires_confirmation": requires_confirmation,
        "profile": public_profile(profile),
        "write_history_path": str(_write_history_path(profile, data_root).resolve()),
        "backup_root": str(_profile_backup_root(profile, data_root).resolve()),
        "retention": {"max_operations": max_operations, "max_bytes": max_bytes},
        "operations_before": len(operations),
        "retained_operations": len(retained_operations),
        "retained_backup_bytes": prune_stats["retained_backup_bytes"],
        "history_operations_to_remove": prune_stats["history_operations_to_remove"],
        "removed_history_operations": removed_history_operations,
        "backup_files_to_remove": backup_files_to_remove,
        "removed_backup_files": removed_backup_files,
        "orphan_files_to_remove": orphan_files_to_remove,
        "removed_orphan_files": removed_orphan_files,
        "total_files_to_remove": backup_files_to_remove + orphan_files_to_remove,
        "removed_total_files": removed_backup_files + removed_orphan_files,
        "prune_orphans": prune_orphans,
        "sample_backup_paths_to_remove": prune_stats["pruned_backup_paths"][:20],
        "sample_orphan_paths_to_remove": [str(path) for path in orphan_candidates[:20]],
        "confirmation_arguments": {
            "profile": profile.name,
            "apply": True,
            "confirm_write": True,
            "prune_orphans": prune_orphans,
            "max_operations": max_operations,
            "max_bytes": max_bytes,
        } if requires_confirmation else None,
        "presentation": {"markdown": "\n".join(lines)},
    }


def _history_operation(profile: RemoteProfile, operation_id: str, root: Path | None = None) -> dict[str, Any]:
    for operation in _load_write_history(profile, root).get("operations", []):
        if isinstance(operation, dict) and operation.get("id") == operation_id:
            return operation
    raise RemoteConfigError(f"Write operation '{operation_id}' was not found for profile '{profile.name}'.")


def _public_operation(operation: dict[str, Any]) -> dict[str, Any]:
    backup_path = operation.get("backup_path")
    public = dict(operation)
    public["undo_available"] = isinstance(backup_path, str) and Path(backup_path).exists()
    return public


def _local_path_for_remote(profile: RemoteProfile, remote_path: str, root: Path | None = None) -> Path:
    relative = _relative_remote_path(profile, remote_path)
    parts = [_safe_path_component(part) for part in relative.split("/") if part not in {"", ".", ".."}]
    return _profile_stage_root(profile, root).joinpath(*parts) if parts else _profile_stage_root(profile, root) / "_root"


def _assert_relative_to(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RemoteConfigError(f"Local path is outside the allowed EQEmu Oracle staging root: {path}") from exc


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _remote_missing_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "550" in message and (
        "no such file" in message
        or "not found" in message
        or "cannot find" in message
        or "file unavailable" in message
        or "path unavailable" in message
    )


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_optional_sha256(value: str | None) -> str:
    return value if value else "(none)"


def _write_audit_payload(
    *,
    kind: str,
    profile: RemoteProfile,
    operation_id: str,
    remote_path: str,
    remote_before_sha256: str | None,
    remote_after_sha256: str | None,
    local_sha256: str | None,
    backup_path: str | None,
    read_only_final: bool,
    undo_available: bool,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "operation_id": operation_id,
        "profile": profile.name,
        "remote_path": remote_path,
        "remote_before_sha256": remote_before_sha256,
        "remote_after_sha256": remote_after_sha256,
        "local_sha256": local_sha256,
        "backup_path": backup_path,
        "undo_available": undo_available,
        "read_only_final": read_only_final,
        "reported_at": _utc_now(),
    }


def _write_audit_markdown(audit: dict[str, Any]) -> str:
    before_sha = audit.get("remote_before_sha256") if isinstance(audit.get("remote_before_sha256"), str) else None
    after_sha = audit.get("remote_after_sha256") if isinstance(audit.get("remote_after_sha256"), str) else None
    return "\n".join(
        [
            "Write audit:",
            f"- Operation id: `{audit.get('operation_id')}`",
            f"- Remote path: `{audit.get('remote_path')}`",
            f"- Before SHA-256: `{_format_optional_sha256(before_sha)}`",
            f"- After SHA-256: `{_format_optional_sha256(after_sha)}`",
            f"- Final read-only mode: `{'on' if audit.get('read_only_final') else 'off'}`",
            f"- Undo restore point: `{'available' if audit.get('undo_available') else 'unavailable'}`",
        ]
    )


def _versioned_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    candidate = path.with_name(f"{stem}.{stamp}{suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{stem}.{stamp}.{counter}{suffix}")
        counter += 1
    return candidate


def _manifest_path(profile: RemoteProfile, root: Path | None = None) -> Path:
    return _profile_stage_root(profile, root) / ".eqemu-oracle-stage-manifest.json"


def _load_stage_manifest(profile: RemoteProfile, root: Path | None = None) -> dict[str, Any]:
    path = _manifest_path(profile, root)
    if not path.exists():
        return {"version": 1, "files": []}
    payload = load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        return {"version": 1, "files": []}
    return payload


def _record_stage_file(profile: RemoteProfile, entry: dict[str, Any], root: Path | None = None) -> None:
    manifest = _load_stage_manifest(profile, root)
    files = [
        item
        for item in manifest["files"]
        if not (isinstance(item, dict) and item.get("local_path") == entry.get("local_path"))
    ]
    files.append(entry)
    manifest["files"] = files
    dump_json(_manifest_path(profile, root), manifest)


def _find_stage_entry(profile: RemoteProfile, local_path: Path, root: Path | None = None) -> dict[str, Any] | None:
    resolved = str(local_path.resolve())
    for item in _load_stage_manifest(profile, root).get("files", []):
        if isinstance(item, dict) and item.get("local_path") == resolved:
            return item
    return None


class _SessionReuseFTP_TLS(ftplib.FTP_TLS):
    def __init__(self, *args: Any, expected_cert_sha256: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.expected_cert_sha256 = _normalize_sha256_fingerprint(expected_cert_sha256)

    def auth(self) -> str:
        response = super().auth()
        self._verify_pinned_certificate()
        return response

    def _verify_pinned_certificate(self) -> None:
        if self.expected_cert_sha256 is None:
            return
        if not isinstance(self.sock, ssl.SSLSocket):
            raise RemoteTransferError("FTPS certificate pin could not be verified because the control socket is not using TLS.")
        certificate = self.sock.getpeercert(binary_form=True)
        if not certificate:
            raise RemoteTransferError("FTPS certificate pin could not be verified because the server did not present a certificate.")
        actual = hashlib.sha256(certificate).hexdigest().upper()
        if actual != self.expected_cert_sha256:
            raise RemoteTransferError(
                f"FTPS certificate fingerprint mismatch for {self.host}. Expected {self.expected_cert_sha256}, got {actual}. Refusing to send credentials."
            )

    def ntransfercmd(self, cmd: str, rest: str | None = None) -> tuple[Any, int | None]:
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            kwargs: dict[str, Any] = {"server_hostname": self.host}
            session = getattr(self.sock, "session", None)
            if session is not None:
                kwargs["session"] = session
            conn = self.context.wrap_socket(conn, **kwargs)
        return conn, size


class FtpClient:
    def __init__(self, profile: RemoteProfile, password: str) -> None:
        self.profile = profile
        self.password = password
        self.client: ftplib.FTP | ftplib.FTP_TLS | None = None

    def __enter__(self) -> "FtpClient":
        if self.profile.protocol == "ftps":
            context = ssl.create_default_context()
            if self.profile.tls_cert_sha256 or not self.profile.verify_tls:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            client: ftplib.FTP | ftplib.FTP_TLS = _SessionReuseFTP_TLS(
                context=context,
                timeout=30,
                expected_cert_sha256=self.profile.tls_cert_sha256,
            )
        else:
            client = ftplib.FTP(timeout=30)
        client.connect(self.profile.host, self.profile.port)
        client.login(self.profile.username, self.password)
        client.set_pasv(self.profile.passive)
        if isinstance(client, ftplib.FTP_TLS):
            client.prot_p()
        self.client = client
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        if self.client is None:
            return
        try:
            self.client.quit()
        except Exception:
            self.client.close()

    def list_files(self, remote_path: str, *, recursive: bool, limit: int, max_depth: int | None = None) -> list[dict[str, Any]]:
        if self.client is None:
            raise RemoteTransferError("FTP client is not connected.")
        collected: list[dict[str, Any]] = []
        self._list_into(remote_path, base_path=remote_path, recursive=recursive, limit=limit, max_depth=max_depth, collected=collected)
        return collected

    def _list_into(
        self,
        remote_path: str,
        *,
        base_path: str,
        recursive: bool,
        limit: int,
        max_depth: int | None,
        collected: list[dict[str, Any]],
    ) -> None:
        if self.client is None or len(collected) >= limit:
            return
        entries = self._list_one(remote_path)
        for entry in entries:
            if len(collected) >= limit:
                break
            collected.append(entry)
            if not recursive or entry.get("type") != "dir":
                continue
            if max_depth is not None and _relative_remote_depth(self.profile, str(entry["path"]), base_path) >= max_depth:
                continue
            self._list_into(
                str(entry["path"]),
                base_path=base_path,
                recursive=recursive,
                limit=limit,
                max_depth=max_depth,
                collected=collected,
            )

    def _list_one(self, remote_path: str) -> list[dict[str, Any]]:
        assert self.client is not None
        try:
            entries: list[dict[str, Any]] = []
            for name, facts in self.client.mlsd(remote_path):
                entry = _entry_from_mlsd(remote_path, name, facts)
                if entry is not None and self._entry_within_profile_root(entry):
                    entries.append(entry)
            return entries
        except Exception:
            return self._fallback_nlst(remote_path)

    def _fallback_nlst(self, remote_path: str) -> list[dict[str, Any]]:
        assert self.client is not None
        names = self.client.nlst(remote_path)
        entries: list[dict[str, Any]] = []
        for name in names:
            path = name if name.startswith("/") else posixpath.join(remote_path, name)
            kind = "file"
            size: int | None = None
            try:
                size_value = self.client.size(path)
                size = int(size_value) if size_value is not None else None
            except Exception:
                kind = "dir" if self._path_is_directory(path) else "unknown"
            entry = {"path": posixpath.normpath(path), "name": posixpath.basename(path), "type": kind, "size": size}
            if self._entry_within_profile_root(entry):
                entries.append(entry)
        return entries

    def _path_is_directory(self, remote_path: str) -> bool:
        assert self.client is not None
        try:
            current = self.client.pwd()
        except Exception:
            current = None
        try:
            self.client.cwd(remote_path)
            return True
        except Exception:
            return False
        finally:
            if current is not None:
                try:
                    self.client.cwd(current)
                except Exception:
                    pass

    def _entry_within_profile_root(self, entry: dict[str, Any]) -> bool:
        path_value = entry.get("path")
        if not isinstance(path_value, str) or not path_value:
            return False
        try:
            resolved = _resolve_remote_path(self.profile, path_value)
        except RemoteConfigError:
            return False
        entry["path"] = resolved
        entry["name"] = posixpath.basename(resolved.rstrip("/")) or resolved
        return True

    def download_bytes(self, remote_path: str, *, max_bytes: int) -> bytes:
        if self.client is None:
            raise RemoteTransferError("FTP client is not connected.")
        chunks: list[bytes] = []
        total = 0

        def collect(chunk: bytes) -> None:
            nonlocal total
            total += len(chunk)
            if total > max_bytes:
                raise RemoteTransferError(f"Remote file is larger than the configured max_bytes limit ({max_bytes}).")
            chunks.append(chunk)

        try:
            self.client.retrbinary(f"RETR {remote_path}", collect)
        except ftplib.error_perm as exc:
            raise RemoteTransferError(f"Unable to download remote path '{remote_path}': {exc}") from exc
        return b"".join(chunks)

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        if self.client is None:
            raise RemoteTransferError("FTP client is not connected.")
        try:
            with local_path.open("rb") as handle:
                self.client.storbinary(f"STOR {remote_path}", handle)
        except ftplib.error_perm as exc:
            raise RemoteTransferError(f"Unable to upload remote path '{remote_path}': {exc}") from exc

    def delete_file(self, remote_path: str) -> None:
        if self.client is None:
            raise RemoteTransferError("FTP client is not connected.")
        try:
            self.client.delete(remote_path)
        except ftplib.error_perm as exc:
            raise RemoteTransferError(f"Unable to delete remote path '{remote_path}': {exc}") from exc


def _entry_from_mlsd(parent: str, name: str, facts: dict[str, str]) -> dict[str, Any] | None:
    path = posixpath.normpath(posixpath.join(parent, name))
    kind = facts.get("type", "unknown")
    if kind in {"cdir", "pdir"}:
        return None
    size = facts.get("size")
    modified = facts.get("modify")
    return {
        "path": path,
        "name": name,
        "type": kind,
        "size": int(size) if isinstance(size, str) and size.isdigit() else None,
        "modified": modified,
    }


def _load_password(profile: RemoteProfile, secret_store: SecretStore | None = None) -> str:
    return (secret_store or SystemSecretStore()).load_password(profile)


def _default_client_factory(profile: RemoteProfile, password: str) -> FtpClient:
    return FtpClient(profile, password)


def _decode_certificate(certificate: bytes) -> dict[str, Any]:
    pem = ssl.DER_cert_to_PEM_cert(certificate)
    details: dict[str, Any] = {"pem": pem}
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="ascii", suffix=".pem", delete=False) as handle:
            handle.write(pem)
            temp_path = Path(handle.name)
        decoded = ssl._ssl._test_decode_cert(str(temp_path))  # type: ignore[attr-defined]
        details.update(
            {
                "subject": decoded.get("subject", []),
                "issuer": decoded.get("issuer", []),
                "not_before": decoded.get("notBefore"),
                "not_after": decoded.get("notAfter"),
                "subject_alt_name": decoded.get("subjectAltName", []),
            }
        )
    except Exception:
        details.update({"subject": [], "issuer": [], "not_before": None, "not_after": None, "subject_alt_name": []})
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
    return details


def inspect_ftps_certificate(profile_name: str, *, config_path: Path | None = None) -> dict[str, Any]:
    profile = load_profile(profile_name, config_path=config_path)
    if profile.protocol != "ftps":
        raise RemoteConfigError("FTPS certificate inspection is only available for ftps profiles.")
    context = ssl._create_unverified_context()
    client = _SessionReuseFTP_TLS(context=context, timeout=30)
    try:
        client.connect(profile.host, profile.port)
        client.auth()
        if not isinstance(client.sock, ssl.SSLSocket):
            raise RemoteTransferError("The FTPS server did not establish a TLS control connection.")
        certificate = client.sock.getpeercert(binary_form=True)
        if not certificate:
            raise RemoteTransferError("The FTPS server did not present a certificate.")
        fingerprint = hashlib.sha256(certificate).hexdigest().upper()
        decoded = _decode_certificate(certificate)
        matches_stored_pin = profile.tls_cert_sha256 == fingerprint if profile.tls_cert_sha256 else None
        return {
            "profile": public_profile(profile),
            "certificate": {
                "sha256": fingerprint,
                "subject": decoded.get("subject", []),
                "issuer": decoded.get("issuer", []),
                "not_before": decoded.get("not_before"),
                "not_after": decoded.get("not_after"),
                "subject_alt_name": decoded.get("subject_alt_name", []),
                "matches_stored_pin": matches_stored_pin,
            },
            "presentation": {
                "markdown": (
                    f"FTPS certificate for `{profile.name}` has SHA-256 fingerprint `{fingerprint}`."
                    + (" It matches the stored pin." if matches_stored_pin is True else "")
                )
            },
        }
    finally:
        try:
            client.quit()
        except Exception:
            client.close()


def trust_ftps_certificate(
    profile_name: str,
    *,
    confirm_trust: bool = False,
    confirm_sha256: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    inspection = inspect_ftps_certificate(profile_name, config_path=config_path)
    profile = load_profile(profile_name, config_path=config_path)
    certificate = inspection["certificate"]
    fingerprint = str(certificate["sha256"])
    if not confirm_trust:
        return {
            "trusted": False,
            "requires_confirmation": True,
            "profile": public_profile(profile),
            "certificate": certificate,
            "confirmation_arguments": {
                "profile": profile.name,
                "confirm_trust": True,
                "confirm_sha256": fingerprint,
            },
            "presentation": {
                "markdown": (
                    f"Trust preview for `{profile.name}`. Confirm only if this FTPS certificate fingerprint is expected: `{fingerprint}`."
                )
            },
        }
    normalized_confirm = _normalize_sha256_fingerprint(confirm_sha256)
    if normalized_confirm != fingerprint:
        raise RemoteConfigError("confirm_sha256 must exactly match the currently presented FTPS certificate SHA-256 fingerprint.")
    payload = _load_profiles(config_path)
    profiles = payload["profiles"]
    existing_payload = profiles.get(profile.name)
    if not isinstance(existing_payload, dict):
        raise RemoteConfigError(f"Remote profile '{profile.name}' does not exist.")
    updated_profile = RemoteProfile(**{**profile.to_dict(), "tls_cert_sha256": fingerprint, "updated_at": _utc_now()})
    profiles[profile.name] = updated_profile.to_dict()
    _write_profiles(payload, config_path)
    return {
        "trusted": True,
        "requires_confirmation": False,
        "profile": public_profile(updated_profile),
        "certificate": {**certificate, "matches_stored_pin": True},
        "profiles_path": str((config_path or profiles_path()).resolve()),
        "presentation": {"markdown": f"Stored pinned FTPS certificate for `{profile.name}`."},
    }


def test_connection(
    profile_name: str,
    *,
    config_path: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    profile = load_profile(profile_name, config_path=config_path)
    password = _load_password(profile, secret_store)
    factory = client_factory or _default_client_factory
    with factory(profile, password) as client:
        entries = client.list_files(profile.root_path, recursive=False, limit=1)
    return {
        "ok": True,
        "profile": public_profile(profile),
        "root_sample_count": len(entries),
        "presentation": {"markdown": f"Connected to `{profile.name}` at `{profile.protocol}://{profile.host}:{profile.port}`."},
    }


def list_remote_files(
    profile_name: str,
    *,
    remote_path: str = ".",
    recursive: bool = False,
    limit: int = 100,
    config_path: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    if limit < 1:
        raise RemoteConfigError("limit must be at least 1.")
    profile = load_profile(profile_name, config_path=config_path)
    resolved_remote_path = _resolve_remote_path(profile, remote_path)
    password = _load_password(profile, secret_store)
    factory = client_factory or _default_client_factory
    with factory(profile, password) as client:
        entries = client.list_files(resolved_remote_path, recursive=recursive, limit=limit)
    lines = [f"Found {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} under `{resolved_remote_path}`."]
    for entry in entries[:20]:
        entry_type = entry.get("type", "unknown")
        size = entry.get("size")
        size_text = f" {size} bytes" if isinstance(size, int) else ""
        lines.append(f"- `{entry.get('path')}` ({entry_type}{size_text})")
    if len(entries) > 20:
        lines.append(f"- ...and {len(entries) - 20} more")
    return {
        "profile": public_profile(profile),
        "remote_path": resolved_remote_path,
        "recursive": recursive,
        "limit": limit,
        "entries": entries,
        "presentation": {"markdown": "\n".join(lines)},
    }


def map_remote_eqemu_server(
    profile_name: str,
    *,
    remote_path: str | None = None,
    scope: str = "auto",
    zone: str | None = None,
    max_depth: int | None = None,
    limit: int = 1000,
    config_path: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    target_path, default_depth = _map_scope_target(scope, remote_path, zone)
    resolved_max_depth = default_depth if max_depth is None else max_depth
    if resolved_max_depth < 0:
        raise RemoteConfigError("max_depth must be at least 0.")
    if limit < 1:
        raise RemoteConfigError("limit must be at least 1.")
    profile = load_profile(profile_name, config_path=config_path)
    resolved_remote_path = _resolve_remote_path(profile, target_path)
    password = _load_password(profile, secret_store)
    factory = client_factory or _default_client_factory
    with factory(profile, password) as client:
        raw_entries = client.list_files(resolved_remote_path, recursive=True, limit=limit, max_depth=resolved_max_depth)
    entries = [_annotate_eqemu_entry(profile, entry) for entry in raw_entries]
    include_expected_missing = resolved_remote_path == profile.root_path and resolved_max_depth >= 3 and len(raw_entries) < limit
    summary = _summarize_eqemu_remote_layout(profile, entries, include_expected_missing=include_expected_missing)
    conflicts = _detect_script_priority_conflicts(profile, entries)
    available_files = _available_file_inventory(entries)
    lines = [
        f"Mapped {len(entries)} remote entr{'y' if len(entries) == 1 else 'ies'} under `{resolved_remote_path}` to depth {resolved_max_depth}.",
        f"Recognized roots: {', '.join(summary['top_level_present']) if summary['top_level_present'] else 'none in this map'}.",
        f"Quest zones seen: {summary['zone_count']}. Scripts seen: Lua {summary['scripts']['lua']}, Perl {summary['scripts']['perl']}.",
    ]
    if available_files:
        file_bits = [f"{name.replace('_', ' ')} {bucket['count']}" for name, bucket in available_files.items()]
        lines.append(f"Available file buckets: {', '.join(file_bits)}.")
    if conflicts:
        lines.append(f"Lua-over-Perl priority conflicts: {len(conflicts)}.")
    if include_expected_missing and summary["expected_missing"]:
        missing = ", ".join(item["path"] for item in summary["expected_missing"][:8])
        lines.append(f"Expected folders not seen in this bounded map: {missing}.")
    if len(entries) >= limit:
        lines.append("The map reached the entry limit; increase `limit` or narrow `remote_path` for more detail.")
    return {
        "profile": public_profile(profile),
        "remote_path": resolved_remote_path,
        "scope": (scope or "auto").strip().lower(),
        "zone": zone,
        "max_depth": resolved_max_depth,
        "limit": limit,
        "truncated": len(entries) >= limit,
        "entries": entries,
        "summary": summary,
        "available_files": available_files,
        "script_priority_conflicts": conflicts,
        "recommended_followups": _map_followups(resolved_remote_path, scope, zone, len(entries) >= limit, explicit_path=remote_path is not None),
        "presentation": {"markdown": "\n".join(lines)},
    }


def stage_remote_file(
    profile_name: str,
    *,
    remote_path: str,
    overwrite_policy: str = "versioned",
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    config_path: Path | None = None,
    data_root: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    if overwrite_policy not in {"versioned", "overwrite", "fail"}:
        raise RemoteConfigError("overwrite_policy must be one of: versioned, overwrite, fail.")
    if max_bytes < 1:
        raise RemoteConfigError("max_bytes must be at least 1.")
    profile = load_profile(profile_name, config_path=config_path)
    resolved_remote_path = _resolve_remote_path(profile, remote_path)
    password = _load_password(profile, secret_store)
    factory = client_factory or _default_client_factory
    with factory(profile, password) as client:
        content = client.download_bytes(resolved_remote_path, max_bytes=max_bytes)

    target_path = _local_path_for_remote(profile, resolved_remote_path, data_root)
    stage_root = _profile_stage_root(profile, data_root)
    _assert_relative_to(target_path, stage_root)
    if target_path.exists():
        if target_path.read_bytes() == content:
            wrote_path = target_path
            action = "unchanged"
        elif overwrite_policy == "fail":
            raise RemoteTransferError(f"Refusing to overwrite existing staged file: {target_path}")
        elif overwrite_policy == "versioned":
            wrote_path = _versioned_path(target_path)
            action = "versioned"
        else:
            wrote_path = target_path
            action = "overwritten"
    else:
        wrote_path = target_path
        action = "created"

    ensure_dir(wrote_path.parent)
    if action != "unchanged":
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(wrote_path.parent), prefix=f".{wrote_path.name}.", suffix=".tmp") as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, wrote_path)

    sha256 = _sha256_bytes(content)
    entry = {
        "profile": profile.name,
        "remote_path": resolved_remote_path,
        "root_path": profile.root_path,
        "local_path": str(wrote_path.resolve()),
        "sha256": sha256,
        "size": len(content),
        "downloaded_at": _utc_now(),
    }
    _record_stage_file(profile, entry, data_root)
    return {
        "staged": True,
        "action": action,
        "profile": public_profile(profile),
        "remote_path": resolved_remote_path,
        "local_path": str(wrote_path.resolve()),
        "size": len(content),
        "sha256": sha256,
        "presentation": {"markdown": f"Staged `{resolved_remote_path}` to `{wrote_path.resolve()}` ({len(content)} bytes)."},
    }


def upload_staged_file(
    profile_name: str,
    *,
    local_path: str,
    remote_path: str | None = None,
    confirm_write: bool = False,
    confirm_remote_path: str | None = None,
    confirm_remote_sha256: str | None = None,
    create_backup: bool = True,
    allow_create: bool = False,
    allow_remote_changed: bool = False,
    max_backup_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    config_path: Path | None = None,
    data_root: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    if not create_backup:
        raise RemoteConfigError("Remote upload requires a local restore-point backup before writing.")
    profile = load_profile(profile_name, config_path=config_path)
    stage_root = _profile_stage_root(profile, data_root)
    source_path = Path(local_path).expanduser()
    _assert_relative_to(source_path, stage_root)
    if not source_path.is_file():
        raise RemoteConfigError(f"Staged local file does not exist: {source_path}")
    stage_entry = _find_stage_entry(profile, source_path, data_root)
    default_remote_path = stage_entry.get("remote_path") if isinstance(stage_entry, dict) else None
    if remote_path is None and default_remote_path is None:
        raise RemoteConfigError("remote_path is required when the local file is not in the EQEmu Oracle staging manifest.")
    target_remote_path = _resolve_remote_path(profile, remote_path or str(default_remote_path or ""))
    source_size = source_path.stat().st_size
    source_sha256 = _sha256_path(source_path)
    staged_sha256 = stage_entry.get("sha256") if isinstance(stage_entry, dict) and isinstance(stage_entry.get("sha256"), str) else None
    normalized_confirm_remote_sha256 = _normalize_sha256_hex(confirm_remote_sha256, field_name="confirm_remote_sha256")
    confirmation_arguments = None
    if not profile.read_only:
        confirmation_arguments = {
            "confirm_write": True,
            "confirm_remote_path": target_remote_path,
        }
        if staged_sha256 is not None:
            confirmation_arguments["confirm_remote_sha256"] = staged_sha256
    preview = {
        "requires_confirmation": True,
        "tool": "upload_eqemu_server_ftp_file",
        "message": (
            "Upload is blocked while this FTP profile is in read-only mode."
            if profile.read_only
            else "Uploading can overwrite a remote server file. Re-run only after explicit user approval with confirm_write=true, confirm_remote_path set exactly to the target remote path, and confirm_remote_sha256 set to the staged or previewed remote hash when overwriting an existing file."
        ),
        "profile": public_profile(profile),
        "local_path": str(source_path.resolve()),
        "remote_path": target_remote_path,
        "local_size": source_size,
        "local_sha256": source_sha256,
        "staged_remote_sha256": staged_sha256,
        "create_backup": True,
        "allow_create": allow_create,
        "allow_remote_changed": allow_remote_changed,
        "read_only_blocked": profile.read_only,
        "confirmation_arguments": confirmation_arguments,
    }
    if not confirm_write:
        return preview
    _assert_profile_allows_write(profile, "remote upload")
    if confirm_remote_path != target_remote_path:
        raise RemoteConfigError("confirm_remote_path must exactly match the resolved remote_path for upload.")
    if source_size > max_backup_bytes:
        raise RemoteConfigError(
            f"Local staged file is {source_size} bytes, which is larger than max_backup_bytes ({max_backup_bytes}). Increase max_backup_bytes before uploading so post-upload verification can complete; no remote write was attempted."
        )

    password = _load_password(profile, secret_store)
    factory = client_factory or _default_client_factory
    operation_id = _new_operation_id("upload", profile, target_remote_path)
    backup_path = _backup_path_for_remote(profile, target_remote_path, data_root, operation_id=operation_id)
    backup_sha256: str
    backup_size: int
    verified_sha256: str
    remote_existed_before = True
    with factory(profile, password) as client:
        try:
            existing = client.download_bytes(target_remote_path, max_bytes=max_backup_bytes)
            ensure_dir(backup_path.parent)
            backup_path.write_bytes(existing)
            backup_sha256 = _sha256_bytes(existing)
            backup_size = len(existing)
            if normalized_confirm_remote_sha256 is None:
                raise RemoteConfigError(
                    "confirm_remote_sha256 is required when uploading over an existing remote file. Use the SHA-256 from the latest staged/downloaded or previewed remote file."
                )
            if normalized_confirm_remote_sha256 != backup_sha256:
                raise RemoteTransferError("Refusing to upload because confirm_remote_sha256 does not match the current remote file hash.")
            if staged_sha256 is not None and backup_sha256 != staged_sha256 and not allow_remote_changed:
                raise RemoteTransferError(
                    "Refusing to upload because the remote file changed since it was staged. Re-stage the file or explicitly allow the changed remote state with the exact current remote SHA-256."
                )
        except RemoteTransferError as exc:
            if not allow_create or not _remote_missing_error(exc):
                if _remote_missing_error(exc):
                    raise RemoteTransferError(
                        f"Remote file '{target_remote_path}' does not exist. Re-run with allow_create=true only if you intend to create this new remote file."
                    ) from exc
                raise
            remote_existed_before = False
            ensure_dir(backup_path.parent)
            backup_path.write_bytes(b"")
            backup_sha256 = _sha256_bytes(b"")
            backup_size = 0
        except RemoteConfigError:
            raise
        except Exception as exc:
            raise RemoteTransferError(f"Refusing to upload because the remote backup could not be created: {exc}") from exc
        client.upload_file(source_path, target_remote_path)
        verified = client.download_bytes(target_remote_path, max_bytes=max_backup_bytes)
        verified_sha256 = _sha256_bytes(verified)
        if verified_sha256 != source_sha256:
            raise RemoteTransferError("Remote upload verification failed: the uploaded file hash does not match the local staged file.")

    operation = {
        "id": operation_id,
        "kind": "upload",
        "profile": profile.name,
        "remote_path": target_remote_path,
        "local_path": str(source_path.resolve()),
        "backup_path": str(backup_path.resolve()),
        "backup_sha256": backup_sha256,
        "backup_size": backup_size,
        "local_sha256": source_sha256,
        "remote_before_sha256": backup_sha256,
        "remote_after_sha256": verified_sha256,
        "remote_existed_before": remote_existed_before,
        "remote_exists_after": True,
        "staged_remote_sha256": staged_sha256,
        "allow_create": allow_create,
        "allow_remote_changed": allow_remote_changed,
        "confirmed_remote_sha256": normalized_confirm_remote_sha256,
        "created_at": _utc_now(),
    }
    _append_write_operation(profile, operation, data_root)

    remote_before_sha256 = backup_sha256 if remote_existed_before else None
    backup_path_text = str(backup_path.resolve())
    audit = _write_audit_payload(
        kind="upload",
        profile=profile,
        operation_id=operation_id,
        remote_path=target_remote_path,
        remote_before_sha256=remote_before_sha256,
        remote_after_sha256=verified_sha256,
        local_sha256=source_sha256,
        backup_path=backup_path_text,
        read_only_final=profile.read_only,
        undo_available=backup_path.is_file(),
    )
    result = {
        "uploaded": True,
        "operation_id": operation_id,
        "profile": public_profile(profile),
        "local_path": str(source_path.resolve()),
        "remote_path": target_remote_path,
        "local_sha256": source_sha256,
        "remote_before_sha256": remote_before_sha256,
        "remote_after_sha256": verified_sha256,
        "backup_path": backup_path_text,
        "backup_sha256": backup_sha256,
        "write_audit": audit,
        "write_history_path": str(_write_history_path(profile, data_root).resolve()),
        "undo_tool": {
            "name": "undo_eqemu_server_ftp_write",
            "preview_arguments": {"profile": profile.name, "operation_id": operation_id},
            "apply_arguments": {"profile": profile.name, "operation_id": operation_id, "confirm_write": True, "confirm_operation_id": operation_id},
        },
        "presentation": {
            "markdown": (
                f"Uploaded `{source_path.resolve()}` to `{target_remote_path}`. Restore point `{operation_id}` saved at `{backup_path.resolve()}`.\n\n"
                f"{_write_audit_markdown(audit)}"
            )
        },
    }
    return result


def upload_staged_file_write_session(
    profile_name: str,
    *,
    local_path: str,
    remote_path: str | None = None,
    confirm_write: bool = False,
    confirm_remote_path: str | None = None,
    confirm_remote_sha256: str | None = None,
    confirm_temporary_read_write: bool = False,
    confirm_final_read_only: bool = False,
    allow_create: bool = False,
    allow_remote_changed: bool = False,
    max_backup_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    config_path: Path | None = None,
    data_root: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    profile = load_profile(profile_name, config_path=config_path)
    preview = upload_staged_file(
        profile_name,
        local_path=local_path,
        remote_path=remote_path,
        confirm_write=False,
        allow_create=allow_create,
        allow_remote_changed=allow_remote_changed,
        max_backup_bytes=max_backup_bytes,
        config_path=config_path,
        data_root=data_root,
        secret_store=secret_store,
        client_factory=client_factory,
    )
    confirmation_arguments = {
        "profile": profile.name,
        "local_path": str(Path(local_path).expanduser()),
        "confirm_write": True,
        "confirm_remote_path": preview["remote_path"],
        "confirm_temporary_read_write": True,
        "confirm_final_read_only": True,
    }
    staged_sha256 = preview.get("staged_remote_sha256")
    if isinstance(staged_sha256, str):
        confirmation_arguments["confirm_remote_sha256"] = staged_sha256
    if remote_path is not None:
        confirmation_arguments["remote_path"] = remote_path
    if allow_create:
        confirmation_arguments["allow_create"] = True
    if allow_remote_changed:
        confirmation_arguments["allow_remote_changed"] = True
    preview.update(
        {
            "tool": "run_eqemu_server_ftp_upload_session",
            "message": (
                "Approved upload session preview. Confirm only after explicit user approval. The session temporarily opens write access if needed, performs one guarded upload, validates it, then re-enables read-only mode in cleanup."
            ),
            "read_only_session": {
                "initial_read_only": profile.read_only,
                "will_temporarily_disable_read_only": profile.read_only,
                "will_reenable_read_only": True,
            },
            "confirmation_arguments": confirmation_arguments,
        }
    )
    if not confirm_write or not confirm_temporary_read_write or not confirm_final_read_only:
        return preview
    if confirm_remote_path != preview["remote_path"]:
        raise RemoteConfigError("confirm_remote_path must exactly match the resolved remote_path for the approved upload session.")

    upload_result: dict[str, Any] | None = None
    upload_error: Exception | None = None
    cleanup_error: Exception | None = None
    temporary_read_write_enabled = False
    try:
        if profile.read_only:
            set_profile_read_only_mode(
                profile.name,
                read_only=False,
                confirm_mode_change=True,
                confirm_profile=profile.name,
                confirm_read_only_mode="read-write",
                config_path=config_path,
            )
            temporary_read_write_enabled = True
        upload_result = upload_staged_file(
            profile_name,
            local_path=local_path,
            remote_path=remote_path,
            confirm_write=True,
            confirm_remote_path=confirm_remote_path,
            confirm_remote_sha256=confirm_remote_sha256,
            create_backup=True,
            allow_create=allow_create,
            allow_remote_changed=allow_remote_changed,
            max_backup_bytes=max_backup_bytes,
            config_path=config_path,
            data_root=data_root,
            secret_store=secret_store,
            client_factory=client_factory,
        )
    except Exception as exc:
        upload_error = exc
    finally:
        try:
            set_profile_read_only_mode(
                profile.name,
                read_only=True,
                confirm_mode_change=True,
                confirm_profile=profile.name,
                confirm_read_only_mode="read-only",
                config_path=config_path,
            )
        except Exception as exc:
            cleanup_error = exc

    final_profile = load_profile(profile.name, config_path=config_path)
    if upload_error is not None:
        if cleanup_error is not None:
            raise RemoteTransferError(f"{upload_error}; additionally failed to re-enable read-only mode: {cleanup_error}") from upload_error
        raise upload_error
    if upload_result is None:
        raise RemoteTransferError("Approved upload session did not produce an upload result.")
    if cleanup_error is not None:
        raise RemoteConfigError(f"Upload succeeded but failed to re-enable read-only mode: {cleanup_error}") from cleanup_error

    read_only_session = {
        "initial_read_only": profile.read_only,
        "temporary_read_write_enabled": temporary_read_write_enabled,
        "cleanup_attempted": True,
        "cleanup_succeeded": True,
        "final_read_only": final_profile.read_only,
    }
    upload_result["read_only_session"] = read_only_session
    upload_result["profile"] = public_profile(final_profile)
    audit = upload_result.get("write_audit")
    if isinstance(audit, dict):
        audit["read_only_final"] = final_profile.read_only
        upload_result["write_audit"] = audit
        presentation = upload_result.get("presentation") if isinstance(upload_result.get("presentation"), dict) else {}
        markdown = presentation.get("markdown") if isinstance(presentation.get("markdown"), str) else ""
        if "Final read-only mode: `off`" in markdown:
            markdown = markdown.replace("Final read-only mode: `off`", "Final read-only mode: `on`")
        upload_result["presentation"] = {
            "markdown": (
                f"{markdown}\n\nRead-only cleanup: `{'on' if final_profile.read_only else 'off'}` "
                f"(temporary read/write {'enabled' if temporary_read_write_enabled else 'was already available'})."
            ).strip()
        }
    return upload_result


def delete_remote_file(
    profile_name: str,
    *,
    remote_path: str,
    confirm_delete: bool = False,
    confirm_remote_path: str | None = None,
    confirm_remote_sha256: str | None = None,
    max_backup_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    config_path: Path | None = None,
    data_root: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    if max_backup_bytes < 1:
        raise RemoteConfigError("max_backup_bytes must be at least 1.")
    profile = load_profile(profile_name, config_path=config_path)
    target_remote_path = _resolve_remote_path(profile, remote_path)
    if profile.read_only and not confirm_delete:
        return {
            "requires_confirmation": True,
            "tool": "delete_eqemu_server_ftp_file",
            "profile": public_profile(profile),
            "remote_path": target_remote_path,
            "read_only_blocked": True,
            "confirmation_arguments": None,
            "message": "Delete is blocked while this FTP profile is in read-only mode.",
            "presentation": {"markdown": f"Delete preview for `{target_remote_path}` is blocked because read-only mode is on."},
        }
    password = _load_password(profile, secret_store)
    factory = client_factory or _default_client_factory
    with factory(profile, password) as client:
        current = client.download_bytes(target_remote_path, max_bytes=max_backup_bytes)
    current_sha256 = _sha256_bytes(current)
    current_size = len(current)
    preview = {
        "requires_confirmation": True,
        "tool": "delete_eqemu_server_ftp_file",
        "message": "Deleting removes a remote server file. Re-run only after explicit user approval with confirm_delete=true, confirm_remote_path set exactly to the target path, and confirm_remote_sha256 set exactly to the current remote hash.",
        "profile": public_profile(profile),
        "remote_path": target_remote_path,
        "remote_sha256": current_sha256,
        "remote_size": current_size,
        "read_only_blocked": profile.read_only,
        "confirmation_arguments": {
            "confirm_delete": True,
            "confirm_remote_path": target_remote_path,
            "confirm_remote_sha256": current_sha256,
        } if not profile.read_only else None,
        "presentation": {
            "markdown": f"Delete preview for `{target_remote_path}` ({current_size} bytes, SHA-256 `{current_sha256}`)."
        },
    }
    if not confirm_delete:
        return preview
    _assert_profile_allows_write(profile, "remote delete")
    if confirm_remote_path != target_remote_path:
        raise RemoteConfigError("confirm_remote_path must exactly match the resolved remote_path for delete.")
    if confirm_remote_sha256 != current_sha256:
        raise RemoteTransferError("Refusing to delete because confirm_remote_sha256 does not match the current remote file hash.")

    operation_id = _new_operation_id("delete", profile, target_remote_path)
    backup_path = _backup_path_for_remote(profile, target_remote_path, data_root, operation_id=operation_id)
    with factory(profile, password) as client:
        current = client.download_bytes(target_remote_path, max_bytes=max_backup_bytes)
        current_sha256 = _sha256_bytes(current)
        current_size = len(current)
        if confirm_remote_sha256 != current_sha256:
            raise RemoteTransferError("Refusing to delete because the remote file changed after preview.")
        ensure_dir(backup_path.parent)
        backup_path.write_bytes(current)
        client.delete_file(target_remote_path)
        try:
            remaining = client.download_bytes(target_remote_path, max_bytes=max_backup_bytes)
        except RemoteTransferError as exc:
            if not _remote_missing_error(exc):
                raise
        else:
            raise RemoteTransferError(
                f"Remote delete verification failed: '{target_remote_path}' is still downloadable ({len(remaining)} bytes)."
            )

    operation = {
        "id": operation_id,
        "kind": "delete",
        "profile": profile.name,
        "remote_path": target_remote_path,
        "backup_path": str(backup_path.resolve()),
        "backup_sha256": current_sha256,
        "backup_size": current_size,
        "local_sha256": None,
        "remote_before_sha256": current_sha256,
        "remote_after_sha256": None,
        "remote_existed_before": True,
        "remote_exists_after": False,
        "created_at": _utc_now(),
    }
    _append_write_operation(profile, operation, data_root)
    backup_path_text = str(backup_path.resolve())
    audit = _write_audit_payload(
        kind="delete",
        profile=profile,
        operation_id=operation_id,
        remote_path=target_remote_path,
        remote_before_sha256=current_sha256,
        remote_after_sha256=None,
        local_sha256=None,
        backup_path=backup_path_text,
        read_only_final=profile.read_only,
        undo_available=backup_path.is_file(),
    )
    return {
        "deleted": True,
        "operation_id": operation_id,
        "profile": public_profile(profile),
        "remote_path": target_remote_path,
        "backup_path": backup_path_text,
        "backup_sha256": current_sha256,
        "remote_before_sha256": current_sha256,
        "remote_after_sha256": None,
        "write_audit": audit,
        "write_history_path": str(_write_history_path(profile, data_root).resolve()),
        "undo_tool": {
            "name": "undo_eqemu_server_ftp_write",
            "preview_arguments": {"profile": profile.name, "operation_id": operation_id},
            "apply_arguments": {"profile": profile.name, "operation_id": operation_id, "confirm_write": True, "confirm_operation_id": operation_id},
        },
        "presentation": {
            "markdown": (
                f"Deleted `{target_remote_path}` after saving restore point `{operation_id}` at `{backup_path.resolve()}`.\n\n"
                f"{_write_audit_markdown(audit)}"
            )
        },
    }


def _backup_path_for_remote(profile: RemoteProfile, remote_path: str, root: Path | None = None, *, operation_id: str | None = None) -> Path:
    stamp = operation_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    relative = _relative_remote_path(profile, remote_path)
    parts = [_safe_path_component(part) for part in relative.split("/") if part not in {"", ".", ".."}]
    base = _profile_backup_root(profile, root) / stamp
    return base.joinpath(*parts) if parts else base / f"_root.{short_hash(remote_path)}"


def list_write_history(
    profile_name: str,
    *,
    limit: int = WRITE_HISTORY_LIMIT,
    config_path: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    if limit < 1:
        raise RemoteConfigError("limit must be at least 1.")
    profile = load_profile(profile_name, config_path=config_path)
    history = _load_write_history(profile, data_root)
    retention = _retention_policy_from_history(history)
    history_preview = dict(history)
    history_preview["operations"] = [operation for operation in history.get("operations", []) if isinstance(operation, dict)]
    _preview_history, gc_stats = _prune_write_history(
        profile,
        history_preview,
        data_root,
        max_operations=retention["max_operations"],
        max_bytes=retention["max_bytes"],
        apply=False,
    )
    operations = [_public_operation(operation) for operation in history.get("operations", []) if isinstance(operation, dict)]
    selected = list(reversed(operations))[:limit]
    lines = [f"{len(selected)} remote write operation{'s' if len(selected) != 1 else ''} for `{profile.name}`."]
    for operation in selected[:20]:
        undo_text = "undo available" if operation.get("undo_available") else "undo unavailable"
        lines.append(f"- `{operation.get('id')}` {operation.get('kind')} `{operation.get('remote_path')}` ({undo_text})")
    if len(selected) > 20:
        lines.append(f"- ...and {len(selected) - 20} more")
    if gc_stats["history_operations_to_remove"] or gc_stats["backup_files_to_remove"]:
        lines.append("Local undo garbage collection is available for entries beyond the retention policy.")
    return {
        "profile": public_profile(profile),
        "operations": selected,
        "count": len(selected),
        "write_history_path": str(_write_history_path(profile, data_root).resolve()),
        "retention": retention,
        "garbage_collection": {
            "needed": bool(gc_stats["history_operations_to_remove"] or gc_stats["backup_files_to_remove"]),
            "history_operations_to_remove": gc_stats["history_operations_to_remove"],
            "backup_files_to_remove": gc_stats["backup_files_to_remove"],
            "tool": "garbage_collect_eqemu_server_ftp_write_history",
            "preview_arguments": {"profile": profile.name},
            "apply_arguments": {"profile": profile.name, "apply": True, "confirm_write": True},
        },
        "presentation": {"markdown": "\n".join(lines)},
    }


def preview_write_undo(
    profile_name: str,
    *,
    operation_id: str,
    check_remote: bool = True,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    config_path: Path | None = None,
    data_root: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    if max_bytes < 1:
        raise RemoteConfigError("max_bytes must be at least 1.")
    profile = load_profile(profile_name, config_path=config_path)
    operation = _public_operation(_history_operation(profile, operation_id, data_root))
    remote_path_value = operation.get("remote_path")
    if not isinstance(remote_path_value, str):
        raise RemoteConfigError(f"Write operation '{operation_id}' does not include a remote path.")
    target_remote_path = _resolve_remote_path(profile, remote_path_value)
    backup_path_value = operation.get("backup_path")
    backup_path = Path(backup_path_value) if isinstance(backup_path_value, str) else None
    undo_available = backup_path is not None and backup_path.is_file()
    current_sha256: str | None = None
    current_matches_expected: bool | None = None
    expected_current_sha256 = operation.get("remote_after_sha256") if isinstance(operation.get("remote_after_sha256"), str) else None
    expected_current_exists = operation.get("remote_exists_after")
    if not isinstance(expected_current_exists, bool):
        expected_current_exists = expected_current_sha256 is not None
    current_exists: bool | None = None
    if check_remote:
        password = _load_password(profile, secret_store)
        factory = client_factory or _default_client_factory
        with factory(profile, password) as client:
            try:
                current = client.download_bytes(target_remote_path, max_bytes=max_bytes)
            except RemoteTransferError as exc:
                if not _remote_missing_error(exc):
                    raise
                current_exists = False
                current_matches_expected = not expected_current_exists
            else:
                current_exists = True
                current_sha256 = _sha256_bytes(current)
                current_matches_expected = expected_current_exists and (
                    expected_current_sha256 is None or current_sha256 == expected_current_sha256
                )
    return {
        "requires_confirmation": True,
        "tool": "undo_eqemu_server_ftp_write",
        "profile": public_profile(profile),
        "operation": operation,
        "remote_path": target_remote_path,
        "undo_available": undo_available,
        "current_remote_sha256": current_sha256,
        "current_remote_exists": current_exists,
        "expected_current_sha256": expected_current_sha256,
        "expected_current_exists": expected_current_exists,
        "current_matches_expected": current_matches_expected,
        "read_only_blocked": profile.read_only,
        "message": (
            "Undo is blocked while this FTP profile is in read-only mode."
            if profile.read_only
            else "Undo restores the saved pre-write backup for this operation. Re-run only after explicit user approval with confirm_write=true and confirm_operation_id set exactly to this operation id."
        ),
        "confirmation_arguments": {
            "confirm_write": True,
            "confirm_operation_id": operation_id,
        } if not profile.read_only else None,
        "presentation": {
            "markdown": (
                f"Undo preview for `{operation_id}` on `{target_remote_path}`. "
                f"Restore point: `{'available' if undo_available else 'missing'}`. "
                f"Current remote matches expected post-write state: `{current_matches_expected}`. "
                f"Read-only mode: `{'on' if profile.read_only else 'off'}`."
            )
        },
    }


def undo_write_operation(
    profile_name: str,
    *,
    operation_id: str,
    confirm_write: bool = False,
    confirm_operation_id: str | None = None,
    allow_remote_changed: bool = False,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    config_path: Path | None = None,
    data_root: Path | None = None,
    secret_store: SecretStore | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    if max_bytes < 1:
        raise RemoteConfigError("max_bytes must be at least 1.")
    profile = load_profile(profile_name, config_path=config_path)
    operation = _history_operation(profile, operation_id, data_root)
    if not confirm_write:
        return preview_write_undo(
            profile_name,
            operation_id=operation_id,
            check_remote=True,
            max_bytes=max_bytes,
            config_path=config_path,
            data_root=data_root,
            secret_store=secret_store,
            client_factory=client_factory,
        )
    _assert_profile_allows_write(profile, "remote undo")
    if confirm_operation_id != operation_id:
        raise RemoteConfigError("confirm_operation_id must exactly match the operation_id for undo.")
    remote_path_value = operation.get("remote_path")
    if not isinstance(remote_path_value, str):
        raise RemoteConfigError(f"Write operation '{operation_id}' does not include a remote path.")
    target_remote_path = _resolve_remote_path(profile, remote_path_value)
    backup_path_value = operation.get("backup_path")
    if not isinstance(backup_path_value, str):
        raise RemoteConfigError(f"Write operation '{operation_id}' does not include a restore-point backup path.")
    restore_path = Path(backup_path_value)
    _assert_relative_to(restore_path, _profile_backup_root(profile, data_root))
    if not restore_path.is_file():
        raise RemoteConfigError(f"Restore-point backup is missing: {restore_path}")
    restore_size = restore_path.stat().st_size
    if restore_size > max_bytes:
        raise RemoteConfigError(
            f"Restore-point backup is {restore_size} bytes, which is larger than max_bytes ({max_bytes}). Increase max_bytes before undoing so post-undo verification can complete; no remote write was attempted."
        )
    restore_sha256 = _sha256_path(restore_path)
    expected_restore_sha256 = operation.get("backup_sha256")
    if isinstance(expected_restore_sha256, str) and restore_sha256 != expected_restore_sha256:
        raise RemoteTransferError("Restore-point backup hash no longer matches the write history record.")

    expected_current_sha256 = operation.get("remote_after_sha256") if isinstance(operation.get("remote_after_sha256"), str) else None
    expected_current_exists = operation.get("remote_exists_after")
    if not isinstance(expected_current_exists, bool):
        expected_current_exists = expected_current_sha256 is not None
    restore_remote_exists = operation.get("remote_existed_before")
    if not isinstance(restore_remote_exists, bool):
        restore_remote_exists = True
    password = _load_password(profile, secret_store)
    factory = client_factory or _default_client_factory
    undo_operation_id = _new_operation_id("undo", profile, target_remote_path)
    undo_backup_path = _backup_path_for_remote(profile, target_remote_path, data_root, operation_id=undo_operation_id)
    current_sha256: str | None = None
    current_size = 0
    current_exists = False
    verified_sha256: str | None = None
    with factory(profile, password) as client:
        try:
            current = client.download_bytes(target_remote_path, max_bytes=max_bytes)
        except RemoteTransferError as exc:
            if not _remote_missing_error(exc):
                raise
            current = b""
            current_exists = False
        else:
            current_exists = True
            current_sha256 = _sha256_bytes(current)
            current_size = len(current)
        current_matches_expected = (
            (not expected_current_exists and not current_exists)
            or (expected_current_exists and current_exists and (expected_current_sha256 is None or current_sha256 == expected_current_sha256))
        )
        if not current_matches_expected and not allow_remote_changed:
            raise RemoteTransferError(
                "Refusing to undo because the remote file changed after the recorded write. Re-stage/review it or explicitly allow the changed remote state."
            )
        ensure_dir(undo_backup_path.parent)
        undo_backup_path.write_bytes(current)
        if restore_remote_exists:
            client.upload_file(restore_path, target_remote_path)
            verified = client.download_bytes(target_remote_path, max_bytes=max_bytes)
            verified_sha256 = _sha256_bytes(verified)
            if verified_sha256 != restore_sha256:
                raise RemoteTransferError("Undo verification failed: the remote file hash does not match the restore-point backup.")
        else:
            if current_exists:
                client.delete_file(target_remote_path)
            try:
                remaining = client.download_bytes(target_remote_path, max_bytes=max_bytes)
            except RemoteTransferError as exc:
                if not _remote_missing_error(exc):
                    raise
            else:
                raise RemoteTransferError(
                    f"Undo verification failed: '{target_remote_path}' is still downloadable ({len(remaining)} bytes)."
                )

    undo_operation = {
        "id": undo_operation_id,
        "kind": "undo",
        "profile": profile.name,
        "remote_path": target_remote_path,
        "previous_operation_id": operation_id,
        "local_path": str(restore_path.resolve()),
        "backup_path": str(undo_backup_path.resolve()),
        "backup_sha256": current_sha256,
        "backup_size": current_size,
        "local_sha256": restore_sha256 if restore_remote_exists else None,
        "remote_before_sha256": current_sha256,
        "remote_after_sha256": verified_sha256,
        "remote_existed_before": current_exists,
        "remote_exists_after": restore_remote_exists,
        "allow_remote_changed": allow_remote_changed,
        "created_at": _utc_now(),
    }
    _append_write_operation(profile, undo_operation, data_root)
    undo_backup_path_text = str(undo_backup_path.resolve())
    audit = _write_audit_payload(
        kind="undo",
        profile=profile,
        operation_id=undo_operation_id,
        remote_path=target_remote_path,
        remote_before_sha256=current_sha256,
        remote_after_sha256=verified_sha256,
        local_sha256=restore_sha256 if restore_remote_exists else None,
        backup_path=undo_backup_path_text,
        read_only_final=profile.read_only,
        undo_available=undo_backup_path.is_file(),
    )
    return {
        "undone": True,
        "operation_id": undo_operation_id,
        "undid_operation_id": operation_id,
        "profile": public_profile(profile),
        "remote_path": target_remote_path,
        "restored_from": str(restore_path.resolve()),
        "backup_path": undo_backup_path_text,
        "remote_before_sha256": current_sha256,
        "remote_after_sha256": verified_sha256,
        "write_audit": audit,
        "write_history_path": str(_write_history_path(profile, data_root).resolve()),
        "presentation": {
            "markdown": (
                f"Restored `{target_remote_path}` from restore point `{operation_id}`. Undo operation `{undo_operation_id}` created a new restore point first.\n\n{_write_audit_markdown(audit)}"
                if restore_remote_exists
                else f"Removed newly-created `{target_remote_path}` while undoing `{operation_id}`. Undo operation `{undo_operation_id}` created a restore point first.\n\n{_write_audit_markdown(audit)}"
            )
        },
    }


def onboarding_payload() -> dict[str, Any]:
    script_path = PLUGIN_ROOT / "scripts" / "eqemu_oracle.py"
    command = f"{sys.executable} {script_path} remote setup"
    markdown = "\n".join(
        [
            "Run the interactive setup locally so the password is entered through a hidden terminal prompt instead of chat:",
            "",
            f"`{command}`",
            "",
            "Use `ftps` when the server supports it. Plain `ftp` is blocked unless you explicitly opt into the insecure transport during setup.",
        ]
    )
    return {
        "recommended_setup_command": command,
        "profiles_path": str(profiles_path().resolve()),
        "staging_root": str(staging_root().resolve()),
        "supported_protocols": list(PROTOCOL_CHOICES),
        "security_notes": [
            "Passwords are stored through the OS credential store when available, or Windows DPAPI on Windows.",
            "Profile metadata and staged files live under the local EQEmu Oracle user-data directory, outside this repository.",
            "Do not paste FTP passwords into chat; use the interactive CLI setup or a local password environment variable.",
            "For self-signed or hostname-mismatched FTPS certificates, use the certificate pinning flow instead of disabling TLS verification.",
            "Remote upload requires a second explicit confirmation, creates a local restore point first, and records a write-history operation id; creating a new remote file additionally requires allow_create=true.",
            "Remote delete requires exact path and SHA-256 confirmation, creates a local restore point first, verifies the file is gone, and records a write-history operation id.",
            "Remote undo requires a second explicit confirmation, checks the current remote state, and creates a new restore point before restoring or removing a created file.",
            "Read-only mode is persisted per profile and blocks upload, delete, undo, and undo garbage-collection apply until explicitly changed with exact confirmation fields.",
            f"Write-history retention defaults to the most recent {WRITE_HISTORY_LIMIT} writes per profile or {WRITE_HISTORY_MAX_BYTES} bytes of restore-point data.",
            "Remote rename, chmod, and directory-removal operations are not exposed.",
        ],
        "presentation": {"markdown": markdown},
    }


def configure_profile_from_env(
    *,
    name: str,
    protocol: str,
    host: str,
    port: int | None,
    username: str,
    root_path: str,
    passive: bool,
    verify_tls: bool,
    allow_insecure: bool,
    password_env_var: str,
    overwrite: bool,
    test_before_save: bool,
) -> dict[str, Any]:
    password = os.environ.get(password_env_var)
    if not password:
        raise RemoteSecretError(f"Environment variable {password_env_var} is not set or is empty.")
    profile = build_profile(
        name=name,
        protocol=protocol,
        host=host,
        port=port,
        username=username,
        root_path=root_path,
        passive=passive,
        verify_tls=verify_tls,
        allow_insecure=allow_insecure,
    )
    if test_before_save:
        with _default_client_factory(profile, password) as client:
            client.list_files(profile.root_path, recursive=False, limit=1)
    return save_profile(profile, password, overwrite=overwrite)


def interactive_setup(args: Any) -> dict[str, Any]:
    profile_name = args.profile or input("Profile name [default]: ").strip() or "default"
    protocol = args.protocol or input("Protocol [ftps]: ").strip() or "ftps"
    host = args.host or input("FTP host: ").strip()
    port = args.port
    if port is None:
        raw_port = input("Port [21]: ").strip()
        port = int(raw_port) if raw_port else 21
    username = args.username or input("Username: ").strip()
    root_path = args.root_path or input("Server root path [/]: ").strip() or "/"
    passive = not bool(args.active)
    verify_tls = not bool(args.no_verify_tls)
    allow_insecure = bool(args.allow_insecure)
    if protocol.lower() == "ftp" and not allow_insecure:
        answer = input("Plain FTP is not encrypted. Type 'allow insecure ftp' to continue: ").strip()
        allow_insecure = answer == "allow insecure ftp"
    profile = build_profile(
        name=profile_name,
        protocol=protocol,
        host=host,
        port=port,
        username=username,
        root_path=root_path,
        passive=passive,
        verify_tls=verify_tls,
        allow_insecure=allow_insecure,
    )
    password = os.environ.get(args.password_env) if args.password_env else None
    if password is None:
        password = getpass.getpass("Password (hidden): ")
    if not args.no_test:
        with _default_client_factory(profile, password) as client:
            client.list_files(profile.root_path, recursive=False, limit=1)
    return save_profile(profile, password, overwrite=bool(args.overwrite))
