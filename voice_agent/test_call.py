#!/usr/bin/env python3
"""
Place exactly ONE outbound SIP call, with no agent attached.

Purpose: prove the telephony layer works in isolation. LiveKit dials out through
the Twilio Elastic SIP trunk and the destination handset rings. Nothing is
listening and nothing speaks -- silence on the line is the successful outcome.

There is deliberately NO retry, NO backoff and NO automatic re-dial anywhere in
this file. Bursts of short-duration calls to India can trigger carrier-side
blocking, and every connected call bills as a rounded whole minute.
One invocation == one call attempt, then exit.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from livekit import api
from livekit.protocol.sip import CreateSIPParticipantRequest

ROOM_NAME = "sip-test"
PARTICIPANT_IDENTITY = "sip-test"
PARTICIPANT_NAME = "Telephony smoke test"


def mask(number: str) -> str:
    """Enough of the number to confirm it's the right one, not the whole thing."""
    if len(number) <= 7:
        return number
    return f"{number[:3]}{'*' * (len(number) - 7)}{number[-4:]}"


def diagnose(status_code, status_text, raw_error) -> str:
    """Map an upstream failure to the layer most likely responsible."""
    blob = f"{status_text or ''} {raw_error}".lower()

    # Twilio surfaces geo-permission blocks as notification 32205 rather than as
    # a distinct SIP status, so match it on the message text.
    if "32205" in blob:
        return (
            "Twilio Geo Permissions -- India is not enabled on the account.\n"
            "  Fix: Twilio Console > Voice > Settings > Geographic Permissions, enable India (IN)."
        )

    if status_code is None:
        return (
            "No SIP response from the provider -- transport or address problem.\n"
            "  Check TWILIO_SIP_TERMINATION_DOMAIN is a bare hostname with no 'sip:' prefix,\n"
            "  and that the trunk's termination domain actually exists in Twilio."
        )

    if status_code == 401:
        return (
            "Credential mismatch.\n"
            "  The username/password on the LiveKit trunk does not match the Twilio\n"
            "  termination credential list. Re-check TWILIO_SIP_AUTH_USERNAME / _PASSWORD."
        )

    if status_code == 403:
        return (
            "Trunk auth rejected, or the US number is not associated with the Twilio trunk.\n"
            "  Check: the credential list is attached to the trunk's Termination settings,\n"
            "  and TWILIO_PHONE_NUMBER is listed under the trunk's Numbers."
        )

    if status_code == 404:
        return (
            "Number not found -- almost always a format problem.\n"
            "  DESTINATION_PHONE_NUMBER must be full E.164 with a leading + (e.g. +91XXXXXXXXXX)."
        )

    if status_code in (480, 486, 487, 603):
        return (
            f"Call reached the network but was not answered (SIP {status_code}).\n"
            "  If the phone rang and then dropped: likely a trial account restriction,\n"
            "  or region pinning not applied (destination_country on the trunk).\n"
            "  If it never rang: the carrier may be filtering the toll-free caller ID."
        )

    if 500 <= status_code < 600:
        return (
            f"Upstream SIP/trunk failure (SIP {status_code}).\n"
            "  Usually a trunk configuration problem on the Twilio side."
        )

    return (
        f"Unmapped SIP status {status_code}.\n"
        "  If the phone rang and then dropped, suspect a trial account restriction\n"
        "  or region pinning not applied."
    )


async def main() -> int:
    load_dotenv()

    trunk_id = (os.getenv("LIVEKIT_OUTBOUND_TRUNK_ID") or "").strip()
    destination = (os.getenv("DESTINATION_PHONE_NUMBER") or "").strip()

    missing = [
        name
        for name, value in (
            ("LIVEKIT_OUTBOUND_TRUNK_ID", trunk_id),
            ("DESTINATION_PHONE_NUMBER", destination),
            ("LIVEKIT_URL", os.getenv("LIVEKIT_URL")),
            ("LIVEKIT_API_KEY", os.getenv("LIVEKIT_API_KEY")),
            ("LIVEKIT_API_SECRET", os.getenv("LIVEKIT_API_SECRET")),
        )
        if not value
    ]
    if missing:
        print(f"ERROR: missing or empty in .env: {', '.join(missing)}", file=sys.stderr)
        if "LIVEKIT_OUTBOUND_TRUNK_ID" in missing:
            print(
                "Create the trunk first with ./create_trunk.sh, then paste the ST_... id into .env.",
                file=sys.stderr,
            )
        return 2

    if not destination.startswith("+"):
        print(
            "ERROR: DESTINATION_PHONE_NUMBER must be full E.164 with a leading +",
            file=sys.stderr,
        )
        return 2

    # LiveKitAPI() reads LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET from
    # the environment, which load_dotenv() has just populated.
    lkapi = api.LiveKitAPI()

    request = CreateSIPParticipantRequest(
        sip_trunk_id=trunk_id,
        sip_call_to=destination,
        room_name=ROOM_NAME,
        participant_identity=PARTICIPANT_IDENTITY,
        participant_name=PARTICIPANT_NAME,
        wait_until_answered=True,
    )

    print(f"Dialing  {mask(destination)}")
    print(f"Trunk    {trunk_id}")
    print(f"Room     {ROOM_NAME}")
    print("No agent attached -- silence on the line is success.")
    print("Blocking until answered (wait_until_answered=True). Ctrl-C to abandon.\n")

    try:
        participant = await lkapi.sip.create_sip_participant(request)
    except api.SipCallError as e:
        # Single attempt only. Report and exit -- never re-dial.
        print("\nCALL FAILED", file=sys.stderr)
        print(f"  sip_status_code : {e.sip_status_code}", file=sys.stderr)
        print(f"  sip_status      : {e.sip_status}", file=sys.stderr)
        print(f"  error           : {e}", file=sys.stderr)
        print(f"\nDiagnosis: {diagnose(e.sip_status_code, e.sip_status, e)}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - surface anything else verbatim
        print("\nCALL FAILED (non-SIP error)", file=sys.stderr)
        print(f"  {type(e).__name__}: {e}", file=sys.stderr)
        print(f"\nDiagnosis: {diagnose(None, None, e)}", file=sys.stderr)
        return 1
    else:
        print("ANSWERED -- the telephony path works.")
        print(f"  participant_id       : {participant.participant_id}")
        print(f"  participant_identity : {participant.participant_identity}")
        print(f"  room_name            : {participant.room_name}")
        print(f"  sip_call_id          : {participant.sip_call_id}")
        return 0
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    # asyncio.run executes main() exactly once. No loop, no retry.
    sys.exit(asyncio.run(main()))
