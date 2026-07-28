# app/services/asterisk_status.py

from __future__ import annotations

from app.services.ami_client import run_cli_command


def get_pjsip_registration_status() -> dict:
    """Map each PJSIP endpoint to its registration status.

    Queried over AMI rather than the `asterisk -rx` CLI, so this works without
    sudo or a local Asterisk binary (for example from inside a container).

    A missing "_error" key means the query succeeded. Endpoints absent from the
    result simply have no registration object.
    """
    lines = run_cli_command("pjsip show registrations")

    if lines is None:
        return {"_error": "Could not reach Asterisk over AMI."}

    statuses = {}

    for line in lines:
        line = line.strip()

        if not line or "/sip:" not in line:
            continue

        parts = line.split()

        if len(parts) < 3:
            continue

        endpoint = parts[0].split("/")[0]
        status = parts[2]

        # CRM generated: vc_cally_77090909_reg -> vc_cally_77090909
        if endpoint.endswith("_reg"):
            endpoint = endpoint[:-4]

        # Older manual naming: mobinet-reg -> mobinet
        if endpoint.endswith("-reg"):
            endpoint = endpoint[:-4]

        statuses[endpoint] = status

    return statuses
