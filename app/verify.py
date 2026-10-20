from dataclasses import asdict, dataclass

from app.chain import ChainClient, grams_to_ng, org_id
from app.signing import receipt_hash, recover_signer


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _recorded_values_check(receipt: dict, record) -> Check:
    """The chain must hold the organisation and range the receipt states."""
    name = "recorded_values_match_receipt"
    try:
        expected_org = org_id(receipt["org"])
        low = grams_to_ng(receipt["emissions_gco2e"]["min"])
        high = grams_to_ng(receipt["emissions_gco2e"]["max"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return Check(name, False, "receipt has no organisation or emissions range to compare")
    if record.org_id != expected_org:
        return Check(name, False, "the chain records a different organisation")
    if (record.gco2e_min_ng, record.gco2e_max_ng) != (low, high):
        return Check(
            name,
            False,
            f"the chain records {record.gco2e_min_ng}-{record.gco2e_max_ng} ng, the receipt states {low}-{high} ng",
        )
    return Check(name, True, "organisation and emissions range match what the chain recorded")


def verify_receipt(receipt: dict, signature: str, chain: ChainClient) -> dict:
    """Independent checks. A receipt is valid only if all of them pass.

    The signer must be the account that recorded this receipt, so a receipt stays valid
    after the issuer role is revoked or the key is rotated.
    Raises ChainError if the chain cannot be read, so callers can tell "invalid" from "unknown".
    """
    try:
        digest = receipt_hash(receipt)
    except (ValueError, TypeError, RecursionError) as exc:
        detail = "receipt is nested too deeply" if isinstance(exc, RecursionError) else str(exc)
        return {
            "valid": False,
            "receipt_hash": None,
            "checks": [asdict(Check("receipt_well_formed", False, detail))],
        }

    checks = [Check("receipt_well_formed", True, "receipt could be hashed")]

    signer = None
    try:
        signer = recover_signer(digest, signature)
        checks.append(Check("signature_recovers_a_signer", True, f"recovered signer {signer}"))
    except Exception:
        checks.append(Check("signature_recovers_a_signer", False, "signature is malformed"))

    record = chain.get_record(digest)

    if record is None:
        checks.append(Check("signer_matches_recorded_issuer", False, "no on-chain record to compare against"))
        checks.append(Check("hash_recorded_on_chain", False, "no on-chain record matches this receipt's hash"))
    else:
        if signer is None:
            checks.append(Check("signer_matches_recorded_issuer", False, "no signer to compare"))
        elif signer == record.issuer:
            checks.append(Check("signer_matches_recorded_issuer", True, f"{signer} recorded this receipt"))
        else:
            checks.append(
                Check("signer_matches_recorded_issuer", False, f"signed by {signer}, recorded by {record.issuer}")
            )
        checks.append(Check("hash_recorded_on_chain", True, "this exact receipt was recorded"))
        checks.append(_recorded_values_check(receipt, record))

    return {
        "valid": all(c.passed for c in checks),
        "receipt_hash": "0x" + digest.hex(),
        "checks": [asdict(c) for c in checks],
    }
