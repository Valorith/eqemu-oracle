from __future__ import annotations

import contextlib
import ftplib
import io
import json
import os
import posixpath
import shutil
import socket
import socketserver
import ssl
import subprocess
import threading
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from eqemu_oracle import cli  # noqa: E402
from eqemu_oracle.remote import (  # noqa: E402
    FtpClient,
    RemoteConfigError,
    RemoteProfile,
    RemoteTransferError,
    _SessionReuseFTP_TLS,
    build_profile,
    delete_remote_file,
    garbage_collect_write_history,
    inspect_ftps_certificate,
    list_profiles,
    list_remote_files,
    list_write_history,
    map_remote_eqemu_server,
    onboarding_payload,
    preview_write_undo,
    save_profile,
    set_profile_read_only_mode,
    stage_remote_file,
    test_connection as remote_test_connection,
    trust_ftps_certificate,
    undo_write_operation,
    upload_staged_file,
)


OPENSSL = shutil.which("openssl")


class FakeSecretStore:
    def __init__(self) -> None:
        self.passwords: dict[str, str] = {}

    def store_password(self, profile: RemoteProfile, password: str) -> dict[str, object]:
        self.passwords[profile.name] = password
        return {"backend": "fake-secret-store", "profile": profile.name}

    def load_password(self, profile: RemoteProfile) -> str:
        return self.passwords[profile.name]

    def delete_password(self, profile: RemoteProfile) -> None:
        self.passwords.pop(profile.name, None)


class FakeClient:
    uploaded: list[tuple[str, bytes]] = []
    initial_files: dict[str, bytes] = {
        "/eqemu/quests/qeynos/Guard_Beren.pl": b"sub EVENT_SAY { quest::say('hail'); }\n",
        "/eqemu/plugins/util.pl": b"sub helper { return 1; }\n",
    }
    files: dict[str, bytes] = dict(initial_files)
    listings: dict[str, list[dict[str, object]]] = {
        "/eqemu": [
            {"path": "/eqemu/quests", "name": "quests", "type": "dir", "size": None},
            {"path": "/eqemu/plugins", "name": "plugins", "type": "dir", "size": None},
        ],
        "/eqemu/quests": [
            {"path": "/eqemu/quests/qeynos", "name": "qeynos", "type": "dir", "size": None},
        ],
    }

    def __init__(self, profile: RemoteProfile, password: str) -> None:
        self.profile = profile
        self.password = password

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        return None

    def list_files(self, remote_path: str, *, recursive: bool, limit: int) -> list[dict[str, object]]:
        entries = list(self.listings.get(remote_path, []))
        if recursive:
            entries.extend(self.listings.get("/eqemu/quests", []))
        return entries[:limit]

    def download_bytes(self, remote_path: str, *, max_bytes: int) -> bytes:
        try:
            data = self.files[remote_path]
        except KeyError as exc:
            raise RemoteTransferError(f"Unable to download remote path '{remote_path}': 550 No such file or directory") from exc
        if len(data) > max_bytes:
            raise RemoteTransferError("too large")
        return data

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        data = local_path.read_bytes()
        self.uploaded.append((remote_path, data))
        self.files[remote_path] = data

    def delete_file(self, remote_path: str) -> None:
        try:
            del self.files[remote_path]
        except KeyError as exc:
            raise RemoteTransferError(f"Unable to delete remote path '{remote_path}': 550 No such file or directory") from exc


def fake_client_factory(profile: RemoteProfile, password: str) -> FakeClient:
    return FakeClient(profile, password)


class FakeMlsdClient:
    def mlsd(self, _remote_path: str) -> list[tuple[str, dict[str, str]]]:
        return [
            (".", {"type": "cdir"}),
            ("..", {"type": "pdir"}),
            ("quests", {"type": "dir", "size": "0"}),
            ("/etc/passwd", {"type": "file", "size": "10"}),
        ]


class FakeTransferPermissionClient:
    def retrbinary(self, _command: str, _callback: object) -> None:
        raise ftplib.error_perm("550 No such file or directory")

    def storbinary(self, _command: str, _handle: object) -> None:
        raise ftplib.error_perm("550 Permission denied")


class FakeTlsContext:
    def __init__(self) -> None:
        self.wrapped_socket: object | None = None
        self.server_hostname: str | None = None
        self.session: object | None = None

    def wrap_socket(self, sock: object, *, server_hostname: str, session: object | None = None) -> str:
        self.wrapped_socket = sock
        self.server_hostname = server_hostname
        self.session = session
        return "wrapped-data-socket"


class FakeControlSocket:
    def __init__(self, session: object) -> None:
        self.session = session


class LoopbackFtpHandler(socketserver.StreamRequestHandler):
    timeout = 5

    def setup(self) -> None:
        super().setup()
        self.cwd = "/"
        self.passive_socket: socket.socket | None = None
        self.protected_data = False

    def finish(self) -> None:
        if self.passive_socket is not None:
            self.passive_socket.close()
        super().finish()

    @property
    def ftp_root(self) -> Path:
        return self.server.ftp_root  # type: ignore[attr-defined]

    @property
    def username(self) -> str:
        return self.server.username  # type: ignore[attr-defined]

    @property
    def password(self) -> str:
        return self.server.password  # type: ignore[attr-defined]

    @property
    def ssl_context(self) -> ssl.SSLContext | None:
        return self.server.ssl_context  # type: ignore[attr-defined]

    def handle(self) -> None:
        self._send("220 Loopback FTP ready")
        while True:
            raw_line = self.rfile.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            command, argument = self._split_command(line)
            if command == "USER":
                self._send("331 Password required" if argument == self.username else "530 Invalid user")
            elif command == "PASS":
                self._send("230 Login successful" if argument == self.password else "530 Invalid password")
            elif command == "SYST":
                self._send("215 UNIX Type: L8")
            elif command == "PWD":
                self._send(f'257 "{self.cwd}" is the current directory')
            elif command == "CWD":
                self._change_directory(argument)
            elif command == "CDUP":
                self._change_directory("..")
            elif command == "TYPE":
                self._send("200 Type set")
            elif command == "AUTH":
                self._authorize_tls(argument)
            elif command == "PBSZ":
                self._send("200 PBSZ=0")
            elif command == "PROT":
                self.protected_data = argument.upper() == "P"
                self._send("200 Protection level set")
            elif command == "PASV":
                self._enter_passive_mode()
            elif command == "EPSV":
                self._enter_extended_passive_mode()
            elif command == "MLSD":
                self._send_mlsd(argument)
            elif command == "NLST":
                self._send_nlst(argument)
            elif command == "SIZE":
                self._send_size(argument)
            elif command == "RETR":
                self._send_file(argument)
            elif command == "STOR":
                self._store_file(argument)
            elif command == "NOOP" or command.startswith("OPTS"):
                self._send("200 OK")
            elif command == "QUIT":
                self._send("221 Goodbye")
                break
            else:
                self._send("502 Command not implemented")

    def _split_command(self, line: str) -> tuple[str, str]:
        if " " not in line:
            return line.upper(), ""
        command, argument = line.split(" ", 1)
        return command.upper(), argument.strip()

    def _send(self, response: str) -> None:
        self.wfile.write((response + "\r\n").encode("utf-8"))
        self.wfile.flush()

    def _authorize_tls(self, value: str) -> None:
        if value.upper() != "TLS" or self.ssl_context is None:
            self._send("502 TLS unavailable")
            return
        self._send("234 AUTH TLS successful")
        self.connection = self.ssl_context.wrap_socket(self.connection, server_side=True)
        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb")

    def _ftp_path(self, value: str) -> str:
        requested = value.strip() or self.cwd
        if requested == ".":
            requested = self.cwd
        if not requested.startswith("/"):
            requested = posixpath.join(self.cwd, requested)
        normalized = posixpath.normpath(requested)
        return "/" if normalized in {"", "."} else normalized

    def _local_path(self, value: str) -> Path:
        ftp_path = self._ftp_path(value)
        candidate = (self.ftp_root / ftp_path.lstrip("/")).resolve()
        root = self.ftp_root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise FileNotFoundError(value) from exc
        return candidate

    def _change_directory(self, value: str) -> None:
        try:
            local_path = self._local_path(value)
        except FileNotFoundError:
            self._send("550 Directory unavailable")
            return
        if not local_path.is_dir():
            self._send("550 Directory unavailable")
            return
        self.cwd = self._ftp_path(value)
        self._send("250 Directory changed")

    def _enter_passive_mode(self) -> None:
        self._close_passive_socket()
        passive_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        passive_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        passive_socket.bind(("127.0.0.1", 0))
        passive_socket.listen(1)
        passive_socket.settimeout(5)
        self.passive_socket = passive_socket
        port = passive_socket.getsockname()[1]
        self._send(f"227 Entering Passive Mode (127,0,0,1,{port // 256},{port % 256})")

    def _enter_extended_passive_mode(self) -> None:
        self._close_passive_socket()
        passive_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        passive_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        passive_socket.bind(("127.0.0.1", 0))
        passive_socket.listen(1)
        passive_socket.settimeout(5)
        self.passive_socket = passive_socket
        self._send(f"229 Entering Extended Passive Mode (|||{passive_socket.getsockname()[1]}|)")

    def _close_passive_socket(self) -> None:
        if self.passive_socket is not None:
            self.passive_socket.close()
            self.passive_socket = None

    def _data_connection(self) -> socket.socket:
        if self.passive_socket is None:
            raise RuntimeError("Passive mode was not established.")
        passive_socket = self.passive_socket
        self.passive_socket = None
        try:
            connection, _address = passive_socket.accept()
            if self.protected_data and self.ssl_context is not None:
                connection = self.ssl_context.wrap_socket(connection, server_side=True)
            return connection
        finally:
            passive_socket.close()

    @contextlib.contextmanager
    def _open_data_connection(self) -> Iterator[socket.socket]:
        connection = self._data_connection()
        try:
            yield connection
        finally:
            if isinstance(connection, ssl.SSLSocket):
                with contextlib.suppress(OSError, ssl.SSLError):
                    connection.unwrap()
            connection.close()

    def _send_mlsd(self, value: str) -> None:
        try:
            local_path = self._local_path(value)
        except FileNotFoundError:
            self._send("550 Path unavailable")
            return
        if not local_path.is_dir():
            self._send("550 Path unavailable")
            return
        self._send("150 Opening data connection")
        with self._open_data_connection() as data_connection:
            lines = []
            for child in sorted(local_path.iterdir(), key=lambda item: item.name):
                kind = "dir" if child.is_dir() else "file"
                size = 0 if child.is_dir() else child.stat().st_size
                lines.append(f"type={kind};size={size}; {child.name}\r\n")
            data_connection.sendall("".join(lines).encode("utf-8"))
        self._send("226 Transfer complete")

    def _send_nlst(self, value: str) -> None:
        try:
            local_path = self._local_path(value)
        except FileNotFoundError:
            self._send("550 Path unavailable")
            return
        if not local_path.is_dir():
            self._send("550 Path unavailable")
            return
        self._send("150 Opening data connection")
        with self._open_data_connection() as data_connection:
            names = "\r\n".join(child.name for child in sorted(local_path.iterdir(), key=lambda item: item.name)) + "\r\n"
            data_connection.sendall(names.encode("utf-8"))
        self._send("226 Transfer complete")

    def _send_size(self, value: str) -> None:
        try:
            local_path = self._local_path(value)
        except FileNotFoundError:
            self._send("550 Path unavailable")
            return
        if not local_path.is_file():
            self._send("550 File unavailable")
            return
        self._send(f"213 {local_path.stat().st_size}")

    def _send_file(self, value: str) -> None:
        try:
            local_path = self._local_path(value)
        except FileNotFoundError:
            self._send("550 Path unavailable")
            return
        if not local_path.is_file():
            self._send("550 File unavailable")
            return
        self._send("150 Opening data connection")
        with self._open_data_connection() as data_connection:
            data_connection.sendall(local_path.read_bytes())
        self._send("226 Transfer complete")

    def _store_file(self, value: str) -> None:
        try:
            local_path = self._local_path(value)
        except FileNotFoundError:
            self._send("550 Path unavailable")
            return
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._send("150 Opening data connection")
        with self._open_data_connection() as data_connection:
            chunks: list[bytes] = []
            while True:
                chunk = data_connection.recv(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        local_path.write_bytes(b"".join(chunks))
        self._send("226 Transfer complete")


class LoopbackFtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, ftp_root: Path, username: str = "eqemu", password: str = "secret", ssl_context: ssl.SSLContext | None = None) -> None:
        super().__init__(("127.0.0.1", 0), LoopbackFtpHandler)
        self.ftp_root = ftp_root
        self.username = username
        self.password = password
        self.ssl_context = ssl_context
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def __enter__(self) -> "LoopbackFtpServer":
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.shutdown()
        self.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


def make_loopback_tls_context(root: Path) -> ssl.SSLContext:
    if OPENSSL is None:
        raise unittest.SkipTest("openssl is required for loopback FTPS tests")
    cert_path = root / "loopback-cert.pem"
    key_path = root / "loopback-key.pem"
    config_path = root / "openssl.cnf"
    config_path.write_text("[req]\ndistinguished_name = req_distinguished_name\n[req_distinguished_name]\n", encoding="utf-8")
    completed = subprocess.run(
        [
            OPENSSL,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-config",
            str(config_path),
            "-subj",
            "/CN=localhost",
            "-days",
            "1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise unittest.SkipTest(f"openssl could not generate a loopback FTPS certificate: {completed.stderr.strip()}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context


class RemoteProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        FakeClient.uploaded = []
        FakeClient.files = dict(FakeClient.initial_files)

    def _save_test_profile(self, root: Path, secret_store: FakeSecretStore) -> Path:
        config_path = root / "server-connections.json"
        profile = build_profile(
            name="live",
            protocol="ftps",
            host="ftp.example.test",
            port=21,
            username="eqemu",
            root_path="/eqemu",
        )
        save_profile(profile, "super-secret-password", config_path=config_path, secret_store=secret_store)
        return config_path

    def _save_loopback_ftp_profile(self, root: Path, server: LoopbackFtpServer, secret_store: FakeSecretStore) -> Path:
        config_path = root / "server-connections.json"
        profile = build_profile(
            name="live",
            protocol="ftp",
            allow_insecure=True,
            host="127.0.0.1",
            port=server.port,
            username=server.username,
            root_path="/eqemu",
        )
        save_profile(profile, server.password, config_path=config_path, secret_store=secret_store)
        return config_path

    def _save_loopback_ftps_profile(self, root: Path, server: LoopbackFtpServer, secret_store: FakeSecretStore) -> Path:
        config_path = root / "server-connections.json"
        profile = build_profile(
            name="live",
            protocol="ftps",
            host="127.0.0.1",
            port=server.port,
            username=server.username,
            root_path="/eqemu",
            verify_tls=False,
        )
        save_profile(profile, server.password, config_path=config_path, secret_store=secret_store)
        return config_path

    def _save_loopback_ftps_pinned_profile(self, root: Path, server: LoopbackFtpServer, secret_store: FakeSecretStore) -> Path:
        config_path = root / "server-connections.json"
        profile = build_profile(
            name="live",
            protocol="ftps",
            host="127.0.0.1",
            port=server.port,
            username=server.username,
            root_path="/eqemu",
            verify_tls=True,
        )
        save_profile(profile, server.password, config_path=config_path, secret_store=secret_store)
        return config_path

    def _run_cli_raw(self, argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.dict(os.environ, env, clear=False),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = cli.main()
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def _run_cli(self, argv: list[str], env: dict[str, str]) -> dict[str, object]:
        exit_code, stdout, stderr = self._run_cli_raw(argv, env)
        if exit_code != 0:
            self.fail(f"CLI exited with {exit_code}\nstdout:\n{stdout}\nstderr:\n{stderr}")
        payload = json.loads(stdout)
        self.assertIsInstance(payload, dict)
        return payload

    def test_onboarding_payload_describes_local_setup_and_guardrails(self) -> None:
        payload = onboarding_payload()
        notes = "\n".join(payload["security_notes"])

        self.assertIn("remote setup", payload["recommended_setup_command"])
        self.assertEqual(payload["supported_protocols"], ["ftps", "ftp"])
        self.assertIn("hidden terminal prompt", payload["presentation"]["markdown"])
        self.assertIn("DPAPI", notes)
        self.assertIn("Do not paste FTP passwords into chat", notes)
        self.assertIn("certificate pinning", notes)
        self.assertIn("Remote upload requires a second explicit confirmation", notes)
        self.assertIn("Remote undo requires a second explicit confirmation", notes)
        self.assertIn("Remote delete requires exact path and SHA-256 confirmation", notes)
        self.assertIn("Remote rename, chmod, and directory-removal operations are not exposed", notes)

    def test_profile_save_uses_secret_reference_without_plaintext_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)

            text = config_path.read_text(encoding="utf-8")
            payload = json.loads(text)

        self.assertNotIn("super-secret-password", text)
        self.assertEqual(payload["profiles"]["live"]["secret_ref"]["backend"], "fake-secret-store")
        self.assertEqual(secret_store.passwords["live"], "super-secret-password")

    def test_plain_ftp_requires_explicit_insecure_opt_in(self) -> None:
        with self.assertRaises(RemoteConfigError):
            build_profile(
                name="plain",
                protocol="ftp",
                host="ftp.example.test",
                username="eqemu",
            )

    def test_list_profiles_redacts_secret_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)

            result = list_profiles(config_path=config_path)

        self.assertEqual(result["count"], 1)
        self.assertTrue(result["profiles"][0]["has_stored_password"])
        self.assertNotIn("secret_ref", result["profiles"][0])
        self.assertFalse(result["profiles"][0]["read_only"])

    def test_read_only_mode_requires_exact_confirmation_and_survives_profile_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)

            preview = set_profile_read_only_mode("live", read_only=True, config_path=config_path)
            self.assertTrue(preview["requires_confirmation"])
            self.assertFalse(preview["current_read_only"])
            self.assertFalse(preview["profile"]["read_only"])
            self.assertFalse(list_profiles(config_path=config_path)["profiles"][0]["read_only"])
            with self.assertRaisesRegex(RemoteConfigError, "does not exist"):
                set_profile_read_only_mode("missing", read_only=True, config_path=config_path)

            with self.assertRaisesRegex(RemoteConfigError, "confirm_profile"):
                set_profile_read_only_mode(
                    "live",
                    read_only=True,
                    confirm_mode_change=True,
                    confirm_profile="wrong",
                    confirm_read_only_mode="read-only",
                    config_path=config_path,
                )
            with self.assertRaisesRegex(RemoteConfigError, "confirm_read_only_mode"):
                set_profile_read_only_mode(
                    "live",
                    read_only=True,
                    confirm_mode_change=True,
                    confirm_profile="live",
                    confirm_read_only_mode="read-write",
                    config_path=config_path,
                )

            enabled = set_profile_read_only_mode(
                "live",
                read_only=True,
                confirm_mode_change=True,
                confirm_profile="live",
                confirm_read_only_mode="read-only",
                config_path=config_path,
            )
            updated_profile = build_profile(
                name="live",
                protocol="ftps",
                host="ftp2.example.test",
                username="eqemu",
                root_path="/eqemu",
            )
            save_profile(updated_profile, "rotated-password", overwrite=True, config_path=config_path, secret_store=secret_store)
            profile_after_update = list_profiles(config_path=config_path)["profiles"][0]

            disabled = set_profile_read_only_mode(
                "live",
                read_only=False,
                confirm_mode_change=True,
                confirm_profile="live",
                confirm_read_only_mode="read-write",
                config_path=config_path,
            )

        self.assertTrue(enabled["changed"])
        self.assertTrue(enabled["profile"]["read_only"])
        self.assertTrue(profile_after_update["read_only"])
        self.assertFalse(disabled["profile"]["read_only"])

    def test_list_remote_files_rejects_paths_outside_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)

            with self.assertRaises(RemoteConfigError):
                list_remote_files(
                    "live",
                    remote_path="../etc",
                    config_path=config_path,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )

    def test_ftp_client_filters_mlsd_current_parent_and_paths_outside_profile_root(self) -> None:
        profile = build_profile(
            name="live",
            protocol="ftps",
            host="ftp.example.test",
            port=21,
            username="eqemu",
            root_path="/eqemu",
        )
        client = FtpClient(profile, "secret")
        client.client = FakeMlsdClient()  # type: ignore[assignment]

        entries = client._list_one("/eqemu")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], "/eqemu/quests")

    def test_ftp_client_wraps_server_transfer_permission_errors(self) -> None:
        profile = build_profile(
            name="live",
            protocol="ftps",
            host="ftp.example.test",
            port=21,
            username="eqemu",
            root_path="/eqemu",
        )
        client = FtpClient(profile, "secret")
        client.client = FakeTransferPermissionClient()  # type: ignore[assignment]
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            local_path = Path(handle.name)
            handle.write(b"test\n")
        self.addCleanup(lambda: local_path.unlink(missing_ok=True))

        with self.assertRaisesRegex(RemoteTransferError, "Unable to download remote path '/eqemu/missing.txt': 550 No such file or directory"):
            client.download_bytes("/eqemu/missing.txt", max_bytes=1024)
        with self.assertRaisesRegex(RemoteTransferError, "Unable to upload remote path '/eqemu/blocked.txt': 550 Permission denied"):
            client.upload_file(local_path, "/eqemu/blocked.txt")

    def test_ftps_data_transfers_reuse_control_channel_tls_session(self) -> None:
        tls_context = FakeTlsContext()
        session = object()
        data_socket = object()
        client = _SessionReuseFTP_TLS(context=tls_context)  # type: ignore[arg-type]
        client.sock = FakeControlSocket(session)  # type: ignore[assignment]
        client.host = "test.rebex.net"
        client._prot_p = True

        with patch.object(ftplib.FTP, "ntransfercmd", return_value=(data_socket, 42)):
            wrapped_socket, size = client.ntransfercmd("MLSD /")

        self.assertEqual(wrapped_socket, "wrapped-data-socket")
        self.assertEqual(size, 42)
        self.assertIs(tls_context.wrapped_socket, data_socket)
        self.assertEqual(tls_context.server_hostname, "test.rebex.net")
        self.assertIs(tls_context.session, session)

    def test_loopback_ftp_stage_upload_history_and_undo_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ftp_root = root / "ftp-root"
            quest_file = ftp_root / "eqemu" / "quests" / "qeynos" / "Guard_Beren.pl"
            plugin_file = ftp_root / "eqemu" / "plugins" / "util.pl"
            quest_file.parent.mkdir(parents=True, exist_ok=True)
            plugin_file.parent.mkdir(parents=True, exist_ok=True)
            original_quest_text = "sub EVENT_SAY { quest::say('hail'); }\n"
            quest_file.write_text(original_quest_text, encoding="utf-8")
            plugin_file.write_text("sub helper { return 1; }\n", encoding="utf-8")
            secret_store = FakeSecretStore()
            with LoopbackFtpServer(ftp_root) as server:
                config_path = self._save_loopback_ftp_profile(root, server, secret_store)

                connection = remote_test_connection("live", config_path=config_path, secret_store=secret_store)
                listing = list_remote_files(
                    "live",
                    remote_path=".",
                    recursive=True,
                    limit=10,
                    config_path=config_path,
                    secret_store=secret_store,
                )
                staged = stage_remote_file(
                    "live",
                    remote_path="quests/qeynos/Guard_Beren.pl",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                )
                staged_path = Path(staged["local_path"])
                updated_quest_text = "sub EVENT_SAY { quest::say('updated over FTP'); }\n"
                staged_path.write_text(updated_quest_text, encoding="utf-8")
                preview = upload_staged_file(
                    "live",
                    local_path=str(staged_path),
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                )
                uploaded = upload_staged_file(
                    "live",
                    local_path=str(staged_path),
                    confirm_write=True,
                    confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                )
                undo_preview = preview_write_undo(
                    "live",
                    operation_id=uploaded["operation_id"],
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                )
                undone = undo_write_operation(
                    "live",
                    operation_id=uploaded["operation_id"],
                    confirm_write=True,
                    confirm_operation_id=uploaded["operation_id"],
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                )
                history = list_write_history("live", config_path=config_path, data_root=root)

            listed_paths = {entry["path"] for entry in listing["entries"]}
            self.assertTrue(connection["ok"])
            self.assertIn("/eqemu/quests/qeynos/Guard_Beren.pl", listed_paths)
            self.assertIn("/eqemu/plugins/util.pl", listed_paths)
            self.assertEqual(staged_path.read_text(encoding="utf-8"), updated_quest_text)
            self.assertTrue(preview["requires_confirmation"])
            self.assertEqual(preview["remote_path"], "/eqemu/quests/qeynos/Guard_Beren.pl")
            self.assertEqual(quest_file.read_text(encoding="utf-8"), original_quest_text)
            self.assertTrue(uploaded["uploaded"])
            self.assertTrue(Path(uploaded["backup_path"]).exists())
            self.assertEqual(Path(uploaded["backup_path"]).read_text(encoding="utf-8"), original_quest_text)
            self.assertEqual(undo_preview["current_matches_expected"], True)
            self.assertTrue(undone["undone"])
            self.assertEqual(quest_file.read_text(encoding="utf-8"), original_quest_text)
            self.assertEqual(history["count"], 2)
            self.assertEqual(history["operations"][0]["kind"], "undo")

    def test_loopback_ftp_map_classifies_eqemu_layout_and_script_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ftp_root = root / "ftp-root"
            files = {
                "eqemu/binaries/current/server.exe": "binary placeholder\n",
                "eqemu/logs/crashes/world_crash.txt": "world crash\n",
                "eqemu/logs/world_123.log": "world log\n",
                "eqemu/logs/zone/qeynos_crash.txt": "zone crash\n",
                "eqemu/logs/zone/qeynos_runtime.log": "zone log\n",
                "eqemu/plugins/Helper.pl": "sub helper {}\n",
                "eqemu/plugins/.git/config": "[core]\n",
                "eqemu/quests/qeynos/Guard_Beren.pl": "sub EVENT_SAY {}\n",
                "eqemu/quests/qeynos/Guard_Beren.lua": "function event_say(e) end\n",
                "eqemu/quests/qeynos/12345.pl": "sub EVENT_SPAWN {}\n",
                "eqemu/quests/global/global_player.pl": "sub EVENT_CONNECT {}\n",
                "eqemu/quests/global/items/1001.lua": "function event_item(e) end\n",
                "eqemu/quests/global/spells/2001.pl": "sub EVENT_SPELL_EFFECT_CLIENT {}\n",
                "eqemu/quests/.git/config": "[core]\n",
            }
            for relative_path, text in files.items():
                target = ftp_root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            secret_store = FakeSecretStore()
            with LoopbackFtpServer(ftp_root) as server:
                config_path = self._save_loopback_ftp_profile(root, server, secret_store)

                mapped = map_remote_eqemu_server(
                    "live",
                    max_depth=5,
                    limit=100,
                    config_path=config_path,
                    secret_store=secret_store,
                )
                zone_map = map_remote_eqemu_server(
                    "live",
                    scope="zone",
                    zone="qeynos",
                    limit=100,
                    config_path=config_path,
                    secret_store=secret_store,
                )
                quests_map = map_remote_eqemu_server(
                    "live",
                    scope="quests",
                    limit=100,
                    config_path=config_path,
                    secret_store=secret_store,
                )
                binaries_map = map_remote_eqemu_server(
                    "live",
                    scope="binaries",
                    limit=100,
                    config_path=config_path,
                    secret_store=secret_store,
                )
                shallow = map_remote_eqemu_server(
                    "live",
                    max_depth=1,
                    limit=100,
                    config_path=config_path,
                    secret_store=secret_store,
                )

        entries_by_path = {entry["path"]: entry for entry in mapped["entries"]}
        conflicts = mapped["script_priority_conflicts"]
        zone_paths = {entry["path"] for entry in zone_map["entries"]}
        quests_paths = {entry["path"] for entry in quests_map["entries"]}
        binaries_paths = {entry["path"] for entry in binaries_map["entries"]}
        shallow_paths = {entry["path"] for entry in shallow["entries"]}

        self.assertFalse(mapped["truncated"])
        self.assertEqual(mapped["summary"]["top_level_present"], ["binaries", "logs", "plugins", "quests"])
        self.assertEqual(mapped["summary"]["zone_count"], 1)
        self.assertIn("qeynos", mapped["summary"]["sample_zones"])
        self.assertEqual(mapped["summary"]["crash_reports"], {"server": 1, "zone": 1})
        self.assertIn("zone_quest_scripts", mapped["available_files"])
        self.assertIn("/eqemu/quests/qeynos/Guard_Beren.lua", mapped["available_files"]["zone_quest_scripts"]["sample_paths"])
        self.assertIn("plugin_scripts", mapped["available_files"])
        self.assertIn("server_logs", mapped["available_files"])
        self.assertIn("zone_logs", mapped["available_files"])
        self.assertEqual(entries_by_path["/eqemu/logs/world_123.log"]["role"], "server_log_file")
        self.assertEqual(entries_by_path["/eqemu/logs/zone/qeynos_runtime.log"]["role"], "zone_log_artifact")
        self.assertEqual(entries_by_path["/eqemu/plugins/Helper.pl"]["role"], "perl_plugin_script")
        self.assertEqual(entries_by_path["/eqemu/quests/qeynos/12345.pl"]["script_target"], "npc_type_id")
        self.assertEqual(entries_by_path["/eqemu/quests/global/items/1001.lua"]["scope"], "global_items")
        self.assertEqual(entries_by_path["/eqemu/quests/global/spells/2001.pl"]["scope"], "global_spells")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["active_path"], "/eqemu/quests/qeynos/Guard_Beren.lua")
        self.assertEqual(conflicts[0]["shadowed_path"], "/eqemu/quests/qeynos/Guard_Beren.pl")
        self.assertEqual(zone_map["remote_path"], "/eqemu/quests/qeynos")
        self.assertEqual(zone_map["scope"], "zone")
        self.assertIn("/eqemu/quests/qeynos/Guard_Beren.lua", zone_paths)
        self.assertNotIn("/eqemu/plugins/Helper.pl", zone_paths)
        self.assertEqual(quests_map["remote_path"], "/eqemu/quests")
        self.assertEqual(quests_map["scope"], "quests")
        self.assertIn("/eqemu/quests/qeynos", quests_paths)
        self.assertNotIn("/eqemu/quests/qeynos/Guard_Beren.lua", quests_paths)
        self.assertEqual(binaries_map["remote_path"], "/eqemu/binaries")
        self.assertEqual(binaries_map["scope"], "binaries")
        self.assertIn("/eqemu/binaries/current", binaries_paths)
        self.assertNotIn("/eqemu/binaries/current/server.exe", binaries_paths)
        self.assertIn("/eqemu/quests", shallow_paths)
        self.assertNotIn("/eqemu/quests/qeynos/Guard_Beren.lua", shallow_paths)

    def test_loopback_ftp_upload_refuses_when_server_file_changed_after_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ftp_root = root / "ftp-root"
            quest_file = ftp_root / "eqemu" / "quests" / "qeynos" / "Guard_Beren.pl"
            quest_file.parent.mkdir(parents=True, exist_ok=True)
            quest_file.write_text("sub EVENT_SAY { quest::say('hail'); }\n", encoding="utf-8")
            secret_store = FakeSecretStore()
            with LoopbackFtpServer(ftp_root) as server:
                config_path = self._save_loopback_ftp_profile(root, server, secret_store)
                staged = stage_remote_file(
                    "live",
                    remote_path="quests/qeynos/Guard_Beren.pl",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                )
                staged_path = Path(staged["local_path"])
                staged_path.write_text("sub EVENT_SAY { quest::say('local update'); }\n", encoding="utf-8")
                changed_remote_text = "sub EVENT_SAY { quest::say('remote changed'); }\n"
                quest_file.write_text(changed_remote_text, encoding="utf-8")

                with self.assertRaisesRegex(RemoteTransferError, "remote file changed"):
                    upload_staged_file(
                        "live",
                        local_path=str(staged_path),
                        confirm_write=True,
                        confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                        config_path=config_path,
                        data_root=root,
                        secret_store=secret_store,
                    )

            self.assertEqual(quest_file.read_text(encoding="utf-8"), changed_remote_text)

    def test_read_only_mode_blocks_upload_before_preview_or_remote_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            staged = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            staged_path = Path(staged["local_path"])
            staged_path.write_text("sub EVENT_SAY { quest::say('blocked'); }\n", encoding="utf-8")
            set_profile_read_only_mode(
                "live",
                read_only=True,
                confirm_mode_change=True,
                confirm_profile="live",
                confirm_read_only_mode="read-only",
                config_path=config_path,
            )

            with self.assertRaisesRegex(RemoteConfigError, "read-only mode"):
                upload_staged_file(
                    "live",
                    local_path=str(staged_path),
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )
            with self.assertRaisesRegex(RemoteConfigError, "read-only mode"):
                upload_staged_file(
                    "live",
                    local_path=str(staged_path),
                    confirm_write=True,
                    confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )

        self.assertEqual(FakeClient.uploaded, [])
        self.assertEqual(FakeClient.files["/eqemu/quests/qeynos/Guard_Beren.pl"], FakeClient.initial_files["/eqemu/quests/qeynos/Guard_Beren.pl"])

    def test_read_only_mode_allows_undo_preview_but_blocks_confirmed_undo_and_gc_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            staged = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            staged_path = Path(staged["local_path"])
            updated_text = b"sub EVENT_SAY { quest::say('uploaded before read only'); }\n"
            staged_path.write_bytes(updated_text)
            uploaded = upload_staged_file(
                "live",
                local_path=str(staged_path),
                confirm_write=True,
                confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            set_profile_read_only_mode(
                "live",
                read_only=True,
                confirm_mode_change=True,
                confirm_profile="live",
                confirm_read_only_mode="read-only",
                config_path=config_path,
            )

            undo_preview = preview_write_undo(
                "live",
                operation_id=uploaded["operation_id"],
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            gc_preview = garbage_collect_write_history("live", config_path=config_path, data_root=root)

            with self.assertRaisesRegex(RemoteConfigError, "read-only mode"):
                undo_write_operation(
                    "live",
                    operation_id=uploaded["operation_id"],
                    confirm_write=True,
                    confirm_operation_id=uploaded["operation_id"],
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )
            with self.assertRaisesRegex(RemoteConfigError, "read-only mode"):
                garbage_collect_write_history(
                    "live",
                    apply=True,
                    confirm_write=True,
                    config_path=config_path,
                    data_root=root,
                )

        self.assertTrue(undo_preview["read_only_blocked"])
        self.assertIsNone(undo_preview["confirmation_arguments"])
        self.assertFalse(gc_preview["applied"])
        self.assertEqual(FakeClient.files["/eqemu/quests/qeynos/Guard_Beren.pl"], updated_text)

    @unittest.skipUnless(OPENSSL is not None, "openssl is required for loopback FTPS tests")
    def test_loopback_ftps_stage_uses_tls_control_and_data_connections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ftp_root = root / "ftp-root"
            quest_file = ftp_root / "eqemu" / "quests" / "qeynos" / "Guard_Beren.pl"
            quest_file.parent.mkdir(parents=True, exist_ok=True)
            quest_file.write_text("sub EVENT_SAY { quest::say('ftps hail'); }\n", encoding="utf-8")
            secret_store = FakeSecretStore()
            tls_context = make_loopback_tls_context(root)
            with LoopbackFtpServer(ftp_root, ssl_context=tls_context) as server:
                config_path = self._save_loopback_ftps_profile(root, server, secret_store)

                connection = remote_test_connection("live", config_path=config_path, secret_store=secret_store)
                listing = list_remote_files(
                    "live",
                    remote_path="quests",
                    recursive=True,
                    config_path=config_path,
                    secret_store=secret_store,
                )
                staged = stage_remote_file(
                    "live",
                    remote_path="quests/qeynos/Guard_Beren.pl",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                )

            self.assertTrue(connection["ok"])
            self.assertEqual(connection["profile"]["protocol"], "ftps")
            self.assertEqual(connection["profile"]["verify_tls"], False)
            self.assertIn("/eqemu/quests/qeynos/Guard_Beren.pl", {entry["path"] for entry in listing["entries"]})
            self.assertEqual(Path(staged["local_path"]).read_text(encoding="utf-8"), "sub EVENT_SAY { quest::say('ftps hail'); }\n")

    @unittest.skipUnless(OPENSSL is not None, "openssl is required for loopback FTPS tests")
    def test_loopback_ftps_certificate_pin_allows_self_signed_hostname_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ftp_root = root / "ftp-root"
            quest_file = ftp_root / "eqemu" / "quests" / "qeynos" / "Guard_Beren.pl"
            quest_file.parent.mkdir(parents=True, exist_ok=True)
            quest_file.write_text("sub EVENT_SAY { quest::say('pinned ftps hail'); }\n", encoding="utf-8")
            secret_store = FakeSecretStore()
            tls_context = make_loopback_tls_context(root)
            with LoopbackFtpServer(ftp_root, ssl_context=tls_context) as server:
                config_path = self._save_loopback_ftps_pinned_profile(root, server, secret_store)

                with self.assertRaises(ssl.SSLCertVerificationError):
                    remote_test_connection("live", config_path=config_path, secret_store=secret_store)
                inspection = inspect_ftps_certificate("live", config_path=config_path)
                preview = trust_ftps_certificate("live", config_path=config_path)
                with self.assertRaisesRegex(RemoteConfigError, "confirm_sha256"):
                    trust_ftps_certificate(
                        "live",
                        confirm_trust=True,
                        confirm_sha256="0" * 64,
                        config_path=config_path,
                    )
                trusted = trust_ftps_certificate(
                    "live",
                    confirm_trust=True,
                    confirm_sha256=inspection["certificate"]["sha256"],
                    config_path=config_path,
                )
                profile_after_pin = list_profiles(config_path=config_path)["profiles"][0]
                updated_profile = build_profile(
                    name="live",
                    protocol="ftps",
                    host="127.0.0.1",
                    port=server.port,
                    username=server.username,
                    root_path="/eqemu",
                    verify_tls=True,
                )
                save_profile(updated_profile, server.password, overwrite=True, config_path=config_path, secret_store=secret_store)
                connection = remote_test_connection("live", config_path=config_path, secret_store=secret_store)

        self.assertTrue(preview["requires_confirmation"])
        self.assertEqual(preview["confirmation_arguments"]["confirm_sha256"], inspection["certificate"]["sha256"])
        self.assertTrue(trusted["trusted"])
        self.assertEqual(trusted["certificate"]["matches_stored_pin"], True)
        self.assertEqual(profile_after_pin["tls_verification_mode"], "pinned-certificate")
        self.assertEqual(profile_after_pin["tls_cert_sha256"], inspection["certificate"]["sha256"])
        self.assertTrue(connection["ok"])
        self.assertEqual(connection["profile"]["tls_cert_sha256"], inspection["certificate"]["sha256"])

    @unittest.skipUnless(sys.platform.startswith("win"), "interactive onboarding storage assertion uses Windows DPAPI")
    def test_cli_interactive_setup_defaults_to_ftps_and_default_profile_without_testing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "eqemu-oracle-home"
            fake_password = "interactive-default-ftps-password"
            env = {"EQEMU_ORACLE_HOME": str(data_root)}
            with (
                patch("builtins.input", side_effect=["", "", "ftp.example.test", "", "eqemu", ""]),
                patch("eqemu_oracle.remote.getpass.getpass", return_value=fake_password) as getpass_prompt,
            ):
                setup = self._run_cli(["eqemu_oracle.py", "remote", "setup", "--no-test"], env)
            profile_store = (data_root / "server-connections.json").read_text(encoding="utf-8")
            profile_payload = json.loads(profile_store)
            profile = profile_payload["profiles"]["default"]

        self.assertTrue(setup["saved"])
        getpass_prompt.assert_called_once_with("Password (hidden): ")
        self.assertEqual(profile["protocol"], "ftps")
        self.assertEqual(profile["host"], "ftp.example.test")
        self.assertEqual(profile["port"], 21)
        self.assertEqual(profile["username"], "eqemu")
        self.assertEqual(profile["root_path"], "/")
        self.assertEqual(profile["verify_tls"], True)
        self.assertEqual(profile["allow_insecure"], False)
        self.assertEqual(profile["secret_ref"]["backend"], "windows-dpapi")
        self.assertNotIn(fake_password, profile_store)

    @unittest.skipUnless(sys.platform.startswith("win"), "interactive onboarding storage assertion uses Windows DPAPI")
    def test_cli_interactive_setup_loopback_ftp_tests_connection_and_saves_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ftp_root = root / "ftp-root"
            data_root = root / "eqemu-oracle-home"
            (ftp_root / "eqemu" / "quests").mkdir(parents=True, exist_ok=True)
            ftp_password = "interactive-loopback-ftp-password"
            with LoopbackFtpServer(ftp_root, password=ftp_password) as server:
                env = {"EQEMU_ORACLE_HOME": str(data_root)}
                prompt_values = [
                    "live",
                    "ftp",
                    "127.0.0.1",
                    str(server.port),
                    server.username,
                    "/eqemu",
                    "allow insecure ftp",
                ]
                with (
                    patch("builtins.input", side_effect=prompt_values),
                    patch("eqemu_oracle.remote.getpass.getpass", return_value=ftp_password) as getpass_prompt,
                ):
                    setup = self._run_cli(["eqemu_oracle.py", "remote", "setup"], env)
                connection = self._run_cli(["eqemu_oracle.py", "remote", "test", "live"], env)
                profile_store = (data_root / "server-connections.json").read_text(encoding="utf-8")
                profile_payload = json.loads(profile_store)
                profile = profile_payload["profiles"]["live"]

        self.assertTrue(setup["saved"])
        self.assertTrue(connection["ok"])
        getpass_prompt.assert_called_once_with("Password (hidden): ")
        self.assertEqual(profile["protocol"], "ftp")
        self.assertEqual(profile["allow_insecure"], True)
        self.assertEqual(profile["host"], "127.0.0.1")
        self.assertEqual(profile["port"], server.port)
        self.assertEqual(profile["root_path"], "/eqemu")
        self.assertEqual(profile["secret_ref"]["backend"], "windows-dpapi")
        self.assertNotIn(ftp_password, profile_store)

    def test_cli_interactive_setup_rejects_plain_ftp_without_exact_opt_in_before_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "eqemu-oracle-home"
            env = {"EQEMU_ORACLE_HOME": str(data_root)}
            prompt_values = ["live", "ftp", "127.0.0.1", "21", "eqemu", "/eqemu", "yes"]
            with (
                patch("builtins.input", side_effect=prompt_values),
                patch("eqemu_oracle.remote.getpass.getpass", return_value="should-not-be-read") as getpass_prompt,
            ):
                exit_code, stdout, stderr = self._run_cli_raw(["eqemu_oracle.py", "remote", "setup"], env)

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Plain FTP sends credentials", stderr)
        getpass_prompt.assert_not_called()
        self.assertFalse((data_root / "server-connections.json").exists())

    def test_cli_interactive_setup_rejects_empty_host_before_password(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "eqemu-oracle-home"
            env = {"EQEMU_ORACLE_HOME": str(data_root)}
            prompt_values = ["live", "", "", "", "eqemu", "/"]
            with (
                patch("builtins.input", side_effect=prompt_values),
                patch("eqemu_oracle.remote.getpass.getpass", return_value="should-not-be-read") as getpass_prompt,
            ):
                exit_code, stdout, stderr = self._run_cli_raw(["eqemu_oracle.py", "remote", "setup"], env)

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Host must be a non-empty hostname", stderr)
        getpass_prompt.assert_not_called()
        self.assertFalse((data_root / "server-connections.json").exists())

    @unittest.skipUnless(sys.platform.startswith("win"), "CLI read-only mode test uses Windows DPAPI credential storage")
    def test_cli_read_only_mode_requires_exact_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "eqemu-oracle-home"
            env = {
                "EQEMU_ORACLE_HOME": str(data_root),
                "EQEMU_TEST_FTP_PASSWORD": "cli-read-only-mode-password",
            }
            self._run_cli(
                [
                    "eqemu_oracle.py",
                    "remote",
                    "setup",
                    "--profile",
                    "live",
                    "--protocol",
                    "ftps",
                    "--host",
                    "ftp.example.test",
                    "--username",
                    "eqemu",
                    "--root-path",
                    "/eqemu",
                    "--password-env",
                    "EQEMU_TEST_FTP_PASSWORD",
                    "--no-test",
                ],
                env,
            )
            preview = self._run_cli(["eqemu_oracle.py", "remote", "read-only", "live", "--enable"], env)
            failed_code, failed_stdout, failed_stderr = self._run_cli_raw(
                [
                    "eqemu_oracle.py",
                    "remote",
                    "read-only",
                    "live",
                    "--enable",
                    "--confirm-mode-change",
                    "--confirm-profile",
                    "live",
                    "--confirm-read-only-mode",
                    "read-write",
                ],
                env,
            )
            enabled = self._run_cli(
                [
                    "eqemu_oracle.py",
                    "remote",
                    "read-only",
                    "live",
                    "--enable",
                    "--confirm-mode-change",
                    "--confirm-profile",
                    "live",
                    "--confirm-read-only-mode",
                    "read-only",
                ],
                env,
            )
            profiles = self._run_cli(["eqemu_oracle.py", "remote", "profiles"], env)

        self.assertTrue(preview["requires_confirmation"])
        self.assertEqual(failed_code, 2)
        self.assertEqual(failed_stdout, "")
        self.assertIn("confirm_read_only_mode", failed_stderr)
        self.assertTrue(enabled["profile"]["read_only"])
        self.assertTrue(profiles["profiles"][0]["read_only"])

    @unittest.skipUnless(sys.platform.startswith("win"), "CLI loopback test uses Windows DPAPI credential storage")
    def test_cli_remote_flow_uses_dpapi_and_loopback_ftp_without_real_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ftp_root = root / "ftp-root"
            data_root = root / "eqemu-oracle-home"
            quest_file = ftp_root / "eqemu" / "quests" / "qeynos" / "Guard_Beren.pl"
            quest_file.parent.mkdir(parents=True, exist_ok=True)
            original_text = "sub EVENT_SAY { quest::say('cli hail'); }\n"
            quest_file.write_text(original_text, encoding="utf-8")
            ftp_password = "loopback-plaintext-password-for-dpapi-test"
            with LoopbackFtpServer(ftp_root, password=ftp_password) as server:
                env = {
                    "EQEMU_ORACLE_HOME": str(data_root),
                    "EQEMU_TEST_FTP_PASSWORD": server.password,
                }
                setup = self._run_cli(
                    [
                        "eqemu_oracle.py",
                        "remote",
                        "setup",
                        "--profile",
                        "live",
                        "--protocol",
                        "ftp",
                        "--allow-insecure",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(server.port),
                        "--username",
                        server.username,
                        "--root-path",
                        "/eqemu",
                        "--password-env",
                        "EQEMU_TEST_FTP_PASSWORD",
                    ],
                    env,
                )
                profile_store = (data_root / "server-connections.json").read_text(encoding="utf-8")
                listing = self._run_cli(["eqemu_oracle.py", "remote", "list", "live", "--remote-path", "quests", "--recursive"], env)
                mapped = self._run_cli(["eqemu_oracle.py", "remote", "map", "live", "--max-depth", "3"], env)
                staged = self._run_cli(["eqemu_oracle.py", "remote", "stage", "live", "quests/qeynos/Guard_Beren.pl"], env)
                staged_path = Path(staged["local_path"])
                updated_text = "sub EVENT_SAY { quest::say('cli updated'); }\n"
                staged_path.write_text(updated_text, encoding="utf-8")
                preview = self._run_cli(["eqemu_oracle.py", "remote", "upload", "live", str(staged_path)], env)
                uploaded = self._run_cli(
                    [
                        "eqemu_oracle.py",
                        "remote",
                        "upload",
                        "live",
                        str(staged_path),
                        "--confirm-write",
                        "--confirm-remote-path",
                        "/eqemu/quests/qeynos/Guard_Beren.pl",
                    ],
                    env,
                )
                undo_preview = self._run_cli(["eqemu_oracle.py", "remote", "undo-preview", "live", uploaded["operation_id"]], env)
                undone = self._run_cli(
                    [
                        "eqemu_oracle.py",
                        "remote",
                        "undo",
                        "live",
                        uploaded["operation_id"],
                        "--confirm-write",
                        "--confirm-operation-id",
                        uploaded["operation_id"],
                    ],
                    env,
                )

            self.assertTrue(setup["saved"])
            profile_payload = json.loads(profile_store)
            secret_ref = profile_payload["profiles"]["live"]["secret_ref"]
            self.assertEqual(secret_ref["backend"], "windows-dpapi")
            self.assertIn("blob", secret_ref)
            self.assertNotIn(ftp_password, profile_store)
            self.assertIn("/eqemu/quests/qeynos/Guard_Beren.pl", {entry["path"] for entry in listing["entries"]})
            self.assertIn("/eqemu/quests/qeynos/Guard_Beren.pl", {entry["path"] for entry in mapped["entries"]})
            self.assertEqual(mapped["entries"][-1]["role"], "quest_script")
            self.assertTrue(preview["requires_confirmation"])
            self.assertTrue(uploaded["uploaded"])
            self.assertEqual(undo_preview["current_matches_expected"], True)
            self.assertTrue(undone["undone"])
            self.assertEqual(quest_file.read_text(encoding="utf-8"), original_text)

    def test_stage_remote_file_writes_to_local_staging_and_versions_existing_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)

            first = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            local_path = Path(first["local_path"])
            local_path.write_text("-- local edit\n", encoding="utf-8")
            second = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )

            manifest = json.loads((root / "staged-files" / "live" / ".eqemu-oracle-stage-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(local_path.exists())
            self.assertEqual(local_path.read_text(encoding="utf-8"), "-- local edit\n")
            self.assertNotEqual(first["local_path"], second["local_path"])
            self.assertEqual(second["action"], "versioned")
            self.assertEqual(len(manifest["files"]), 2)

    def test_stage_remote_file_can_refuse_local_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)

            first = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            Path(first["local_path"]).write_text("-- local edit\n", encoding="utf-8")

            with self.assertRaises(RemoteTransferError):
                stage_remote_file(
                    "live",
                    remote_path="quests/qeynos/Guard_Beren.pl",
                    overwrite_policy="fail",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )

    def test_upload_requires_confirmation_and_backs_up_remote_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            staged = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            local_path = Path(staged["local_path"])
            local_path.write_text("sub EVENT_SAY { quest::say('updated'); }\n", encoding="utf-8")

            preview = upload_staged_file(
                "live",
                local_path=str(local_path),
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            with self.assertRaises(RemoteConfigError):
                upload_staged_file(
                    "live",
                    local_path=str(local_path),
                    confirm_write=True,
                    confirm_remote_path="/eqemu/wrong.pl",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )
            uploaded = upload_staged_file(
                "live",
                local_path=str(local_path),
                confirm_write=True,
                confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            self.assertTrue(Path(uploaded["backup_path"]).exists())
            self.assertIn("hail", Path(uploaded["backup_path"]).read_text(encoding="utf-8"))
            history = list_write_history("live", config_path=config_path, data_root=root)

        self.assertTrue(preview["requires_confirmation"])
        self.assertIn("operation_id", uploaded)
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["operations"][0]["id"], uploaded["operation_id"])
        self.assertTrue(history["operations"][0]["undo_available"])
        self.assertEqual(FakeClient.uploaded[0][0], "/eqemu/quests/qeynos/Guard_Beren.pl")

    def test_upload_refuses_when_remote_changed_since_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            staged = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            local_path = Path(staged["local_path"])
            local_path.write_text("sub EVENT_SAY { quest::say('updated'); }\n", encoding="utf-8")
            FakeClient.files["/eqemu/quests/qeynos/Guard_Beren.pl"] = b"remote changed\n"

            with self.assertRaises(RemoteTransferError):
                upload_staged_file(
                    "live",
                    local_path=str(local_path),
                    confirm_write=True,
                    confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )

            uploaded = upload_staged_file(
                "live",
                local_path=str(local_path),
                confirm_write=True,
                confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                allow_remote_changed=True,
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )

        self.assertTrue(uploaded["uploaded"])
        self.assertEqual(FakeClient.uploaded[0][0], "/eqemu/quests/qeynos/Guard_Beren.pl")

    def test_upload_can_create_new_file_only_with_allow_create_and_undo_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            local_path = root / "staged-files" / "live" / "quests" / "global" / "textFile.txt"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("created by guarded upload\n", encoding="utf-8")
            remote_path = "/eqemu/quests/global/textFile.txt"
            FakeClient.files.pop(remote_path, None)

            with self.assertRaisesRegex(RemoteTransferError, "allow_create=true"):
                upload_staged_file(
                    "live",
                    local_path=str(local_path),
                    remote_path=remote_path,
                    confirm_write=True,
                    confirm_remote_path=remote_path,
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )
            uploaded = upload_staged_file(
                "live",
                local_path=str(local_path),
                remote_path=remote_path,
                confirm_write=True,
                confirm_remote_path=remote_path,
                allow_create=True,
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            preview = preview_write_undo(
                "live",
                operation_id=uploaded["operation_id"],
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            undone = undo_write_operation(
                "live",
                operation_id=uploaded["operation_id"],
                confirm_write=True,
                confirm_operation_id=uploaded["operation_id"],
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            history = list_write_history("live", config_path=config_path, data_root=root)

        self.assertTrue(uploaded["uploaded"])
        self.assertFalse(history["operations"][1]["remote_existed_before"])
        self.assertTrue(preview["current_matches_expected"])
        self.assertFalse(undone["remote_after_sha256"])
        self.assertNotIn(remote_path, FakeClient.files)

    def test_delete_requires_exact_hash_backup_and_can_be_undone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            remote_path = "/eqemu/quests/global/textFile.txt"
            FakeClient.files[remote_path] = b"delete me safely\n"

            preview = delete_remote_file(
                "live",
                remote_path=remote_path,
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            with self.assertRaisesRegex(RemoteTransferError, "confirm_remote_sha256"):
                delete_remote_file(
                    "live",
                    remote_path=remote_path,
                    confirm_delete=True,
                    confirm_remote_path=remote_path,
                    confirm_remote_sha256="0" * 64,
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )
            deleted = delete_remote_file(
                "live",
                remote_path=remote_path,
                confirm_delete=True,
                confirm_remote_path=remote_path,
                confirm_remote_sha256=preview["remote_sha256"],
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            backup_exists = Path(deleted["backup_path"]).exists()
            undo_preview = preview_write_undo(
                "live",
                operation_id=deleted["operation_id"],
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            undone = undo_write_operation(
                "live",
                operation_id=deleted["operation_id"],
                confirm_write=True,
                confirm_operation_id=deleted["operation_id"],
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )

        self.assertTrue(deleted["deleted"])
        self.assertTrue(backup_exists)
        self.assertTrue(undo_preview["current_matches_expected"])
        self.assertEqual(FakeClient.files[remote_path], b"delete me safely\n")
        self.assertTrue(undone["remote_after_sha256"])

    def test_upload_refuses_large_local_file_before_remote_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            staged = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            local_path = Path(staged["local_path"])
            local_path.write_text("sub EVENT_SAY { quest::say('this is too large for the limit'); }\n", encoding="utf-8")

            with self.assertRaisesRegex(RemoteConfigError, "Local staged file"):
                upload_staged_file(
                    "live",
                    local_path=str(local_path),
                    confirm_write=True,
                    confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                    max_backup_bytes=4,
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )

        self.assertEqual(FakeClient.uploaded, [])
        self.assertEqual(FakeClient.files["/eqemu/quests/qeynos/Guard_Beren.pl"], FakeClient.initial_files["/eqemu/quests/qeynos/Guard_Beren.pl"])

    def test_garbage_collect_write_history_previews_then_removes_old_restore_points(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            backup_profile_root = root / "remote-backups" / "live"
            operations = []
            backup_paths = []
            for index in range(4):
                backup_path = backup_profile_root / f"op-{index}" / "quests" / f"npc-{index}.pl"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_text(f"old {index}\n", encoding="utf-8")
                backup_paths.append(backup_path)
                operations.append(
                    {
                        "id": f"op-{index}",
                        "kind": "upload",
                        "profile": "live",
                        "remote_path": f"/eqemu/quests/npc-{index}.pl",
                        "backup_path": str(backup_path),
                        "backup_size": backup_path.stat().st_size,
                        "created_at": f"2026-01-01T00:00:0{index}Z",
                    }
                )
            history_path = backup_profile_root / "write-history.json"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(json.dumps({"version": 1, "operations": operations}), encoding="utf-8")

            preview = garbage_collect_write_history(
                "live",
                max_operations=2,
                config_path=config_path,
                data_root=root,
                prune_orphans=False,
            )
            confirmation = garbage_collect_write_history(
                "live",
                apply=True,
                max_operations=2,
                config_path=config_path,
                data_root=root,
                prune_orphans=False,
            )
            self.assertFalse(preview["applied"])
            self.assertEqual(preview["history_operations_to_remove"], 2)
            self.assertEqual(preview["backup_files_to_remove"], 2)
            self.assertTrue(confirmation["requires_confirmation"])
            self.assertTrue(all(path.exists() for path in backup_paths))

            applied = garbage_collect_write_history(
                "live",
                apply=True,
                confirm_write=True,
                max_operations=2,
                config_path=config_path,
                data_root=root,
                prune_orphans=False,
            )
            updated_history = json.loads(history_path.read_text(encoding="utf-8"))

            self.assertEqual(applied["removed_history_operations"], 2)
            self.assertEqual(applied["removed_backup_files"], 2)
            self.assertFalse(backup_paths[0].exists())
            self.assertFalse(backup_paths[1].exists())
            self.assertTrue(backup_paths[2].exists())
            self.assertTrue(backup_paths[3].exists())
            self.assertEqual([operation["id"] for operation in updated_history["operations"]], ["op-2", "op-3"])

    def test_garbage_collect_write_history_removes_orphaned_local_backup_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            backup_profile_root = root / "remote-backups" / "live"
            referenced = backup_profile_root / "op-keep" / "quests" / "npc.pl"
            orphan = backup_profile_root / "manual" / "old-restore-point.tmp"
            referenced.parent.mkdir(parents=True, exist_ok=True)
            orphan.parent.mkdir(parents=True, exist_ok=True)
            referenced.write_text("referenced\n", encoding="utf-8")
            orphan.write_text("orphan\n", encoding="utf-8")
            history_path = backup_profile_root / "write-history.json"
            history_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "operations": [
                            {
                                "id": "op-keep",
                                "kind": "upload",
                                "profile": "live",
                                "remote_path": "/eqemu/quests/npc.pl",
                                "backup_path": str(referenced),
                                "backup_size": referenced.stat().st_size,
                                "created_at": "2026-01-01T00:00:00Z",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            preview = garbage_collect_write_history("live", config_path=config_path, data_root=root)
            self.assertEqual(preview["orphan_files_to_remove"], 1)
            self.assertTrue(orphan.exists())

            applied = garbage_collect_write_history(
                "live",
                apply=True,
                confirm_write=True,
                config_path=config_path,
                data_root=root,
            )

            self.assertEqual(applied["removed_orphan_files"], 1)
            self.assertFalse(orphan.exists())
            self.assertTrue(referenced.exists())
            self.assertTrue(history_path.exists())

    def test_list_write_history_uses_stored_retention_policy_for_gc_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            backup_profile_root = root / "remote-backups" / "live"
            operations = []
            for index in range(3):
                backup_path = backup_profile_root / f"op-{index}" / f"npc-{index}.pl"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_text(f"old {index}\n", encoding="utf-8")
                operations.append(
                    {
                        "id": f"op-{index}",
                        "kind": "upload",
                        "profile": "live",
                        "remote_path": f"/eqemu/npc-{index}.pl",
                        "backup_path": str(backup_path),
                        "backup_size": backup_path.stat().st_size,
                        "created_at": f"2026-01-01T00:00:0{index}Z",
                    }
                )
            history_path = backup_profile_root / "write-history.json"
            history_path.write_text(
                json.dumps({"version": 1, "retention": {"max_operations": 2, "max_bytes": 1024}, "operations": operations}),
                encoding="utf-8",
            )

            history = list_write_history("live", config_path=config_path, data_root=root, limit=10)

        self.assertEqual(history["retention"], {"max_operations": 2, "max_bytes": 1024})
        self.assertTrue(history["garbage_collection"]["needed"])
        self.assertEqual(history["garbage_collection"]["history_operations_to_remove"], 1)

    def test_undo_write_operation_restores_backup_and_records_new_restore_point(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            staged = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            local_path = Path(staged["local_path"])
            local_path.write_text("sub EVENT_SAY { quest::say('updated'); }\n", encoding="utf-8")
            uploaded = upload_staged_file(
                "live",
                local_path=str(local_path),
                confirm_write=True,
                confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            FakeClient.uploaded = []

            preview = preview_write_undo(
                "live",
                operation_id=uploaded["operation_id"],
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            with self.assertRaises(RemoteConfigError):
                undo_write_operation(
                    "live",
                    operation_id=uploaded["operation_id"],
                    confirm_write=True,
                    confirm_operation_id="wrong",
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )
            with self.assertRaisesRegex(RemoteConfigError, "Restore-point backup"):
                undo_write_operation(
                    "live",
                    operation_id=uploaded["operation_id"],
                    confirm_write=True,
                    confirm_operation_id=uploaded["operation_id"],
                    max_bytes=4,
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )
            undone = undo_write_operation(
                "live",
                operation_id=uploaded["operation_id"],
                confirm_write=True,
                confirm_operation_id=uploaded["operation_id"],
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            history = list_write_history("live", config_path=config_path, data_root=root)

        self.assertTrue(preview["requires_confirmation"])
        self.assertTrue(preview["current_matches_expected"])
        self.assertEqual(FakeClient.uploaded[0][0], "/eqemu/quests/qeynos/Guard_Beren.pl")
        self.assertTrue(undone["undone"])
        self.assertEqual(FakeClient.files["/eqemu/quests/qeynos/Guard_Beren.pl"], FakeClient.initial_files["/eqemu/quests/qeynos/Guard_Beren.pl"])
        self.assertEqual(history["count"], 2)
        self.assertEqual(history["operations"][0]["kind"], "undo")

    def test_undo_refuses_when_remote_changed_after_recorded_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secret_store = FakeSecretStore()
            config_path = self._save_test_profile(root, secret_store)
            staged = stage_remote_file(
                "live",
                remote_path="quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            local_path = Path(staged["local_path"])
            local_path.write_text("sub EVENT_SAY { quest::say('updated'); }\n", encoding="utf-8")
            uploaded = upload_staged_file(
                "live",
                local_path=str(local_path),
                confirm_write=True,
                confirm_remote_path="/eqemu/quests/qeynos/Guard_Beren.pl",
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )
            FakeClient.files["/eqemu/quests/qeynos/Guard_Beren.pl"] = b"changed after upload\n"

            with self.assertRaises(RemoteTransferError):
                undo_write_operation(
                    "live",
                    operation_id=uploaded["operation_id"],
                    confirm_write=True,
                    confirm_operation_id=uploaded["operation_id"],
                    config_path=config_path,
                    data_root=root,
                    secret_store=secret_store,
                    client_factory=fake_client_factory,
                )

            undone = undo_write_operation(
                "live",
                operation_id=uploaded["operation_id"],
                confirm_write=True,
                confirm_operation_id=uploaded["operation_id"],
                allow_remote_changed=True,
                config_path=config_path,
                data_root=root,
                secret_store=secret_store,
                client_factory=fake_client_factory,
            )

        self.assertTrue(undone["undone"])


if __name__ == "__main__":
    unittest.main()
