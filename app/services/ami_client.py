# app/services/ami_client.py

from __future__ import annotations

import socket
import uuid
from typing import List, Optional

from app.config import settings


class AMIError(Exception):
    """Raised when an AMI conversation cannot be completed."""


class AMIClient:
    """Minimal Asterisk Manager Interface client for request/response actions.

    This exists so status queries do not have to shell out to `asterisk -rx`,
    which needs the CLI binary, sudo, and a local Asterisk. Over AMI the same
    queries work from anywhere the manager port is reachable, including from
    inside a container.

    Intended for short request/response exchanges. The event stream is handled
    separately by run_listener.py, which keeps its own long lived connection.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 5.0,
    ):
        self.host = host or settings.ASTERISK_AMI_HOST
        self.port = int(port or settings.ASTERISK_AMI_PORT)
        self.username = username or settings.ASTERISK_AMI_USER
        self.password = password or settings.ASTERISK_AMI_PASS
        self.timeout = float(timeout)
        self._sock: Optional[socket.socket] = None

    # -- connection ------------------------------------------------------

    def __enter__(self) -> "AMIClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)

        # Send Login straight away without reading the greeting first. The
        # greeting is a single line with no blank line after it, so reading it
        # on its own would always block until the timeout expired. Reading the
        # login reply consumes the greeting along with it.
        response = self._send(
            {
                "Action": "Login",
                "Username": self.username,
                "Secret": self.password,
                "Events": "off",
            }
        )

        if "Response: Success" not in response:
            self.close()
            raise AMIError(f"AMI login failed for user {self.username!r}")

    def close(self) -> None:
        if not self._sock:
            return

        try:
            self._sock.sendall(b"Action: Logoff\r\n\r\n")
        except Exception:
            pass

        try:
            self._sock.close()
        except Exception:
            pass

        self._sock = None

    # -- protocol --------------------------------------------------------

    def _read_until_blank(self, wait: Optional[float] = None, tolerate_timeout: bool = False) -> str:
        """Read one AMI block, which is terminated by a blank line."""
        if not self._sock:
            raise AMIError("Not connected to AMI.")

        self._sock.settimeout(wait or self.timeout)
        chunks: List[bytes] = []

        while True:
            try:
                data = self._sock.recv(8192)
            except socket.timeout:
                if tolerate_timeout or chunks:
                    break
                raise AMIError("Timed out waiting for AMI response.")

            if not data:
                break

            chunks.append(data)

            if b"\r\n\r\n" in b"".join(chunks):
                break

        return b"".join(chunks).decode("utf-8", errors="replace")

    def _send(self, fields: dict) -> str:
        if not self._sock:
            raise AMIError("Not connected to AMI.")

        payload = ""

        for key, value in fields.items():
            if value is None:
                continue

            clean = str(value).replace("\r", " ").replace("\n", " ")
            payload += f"{key}: {clean}\r\n"

        payload += "\r\n"

        self._sock.sendall(payload.encode("utf-8"))
        return self._read_until_blank()

    # -- actions ---------------------------------------------------------

    def command(self, cli_command: str) -> List[str]:
        """Run an Asterisk CLI command over AMI and return its output lines.

        Asterisk 16 answers Action: Command with the CLI output split across
        "Output: " prefixed lines. Older builds instead inline the text and end
        it with "--END COMMAND--", so both shapes are handled.
        """
        response = self._send(
            {
                "Action": "Command",
                "Command": cli_command,
                "ActionID": f"cmd-{uuid.uuid4().hex[:12]}",
            }
        )

        if "Response: Error" in response:
            raise AMIError(f"AMI rejected command {cli_command!r}")

        lines: List[str] = []
        saw_output_prefix = False

        for raw_line in response.splitlines():
            if raw_line.startswith("Output: "):
                saw_output_prefix = True
                lines.append(raw_line[len("Output: "):])
            elif raw_line == "Output:":
                saw_output_prefix = True
                lines.append("")

        if saw_output_prefix:
            return lines

        # Legacy layout: everything after the headers is the command output.
        for raw_line in response.splitlines():
            if raw_line.startswith(("Response:", "ActionID:", "Message:", "Privilege:")):
                continue
            if raw_line.strip() == "--END COMMAND--":
                break
            lines.append(raw_line)

        return lines


def run_cli_command(cli_command: str, timeout: float = 5.0) -> Optional[List[str]]:
    """Run one CLI command over AMI, returning None if Asterisk is unreachable.

    Callers treat None as "status unknown" rather than "nothing found", so a
    dropped AMI connection is never mistaken for an empty result.
    """
    try:
        with AMIClient(timeout=timeout) as client:
            return client.command(cli_command)

    except (AMIError, socket.error, OSError) as exc:
        print(f"AMI command failed ({cli_command!r}): {exc}")
        return None
