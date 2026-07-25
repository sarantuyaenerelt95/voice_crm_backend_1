# app/services/asterisk_status.py

from __future__ import annotations

import subprocess


def get_pjsip_registration_status() -> dict:
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/sbin/asterisk", "-rx", "pjsip show registrations"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        output = result.stdout or result.stderr or ""

        if result.returncode != 0:
            return {
                "_error": output.strip() or f"asterisk command failed with code {result.returncode}",
            }

    except Exception as exc:
        return {"_error": str(exc)}

    statuses = {}

    for line in output.splitlines():
        line = line.strip()

        if not line:
            continue

        if "/sip:" not in line:
            continue

        parts = line.split()

        if len(parts) < 3:
            continue

        reg_name = parts[0].split("/")[0]
        status = parts[2]

        endpoint = reg_name

        # CRM generated: vc_cally_77090909_reg -> vc_cally_77090909
        if endpoint.endswith("_reg"):
            endpoint = endpoint[:-4]

        # Old manual: mobinet-reg -> mobinet
        if endpoint.endswith("-reg"):
            endpoint = endpoint[:-4]

        statuses[endpoint] = status

    return statuses