import re

# Patterns that indicate a safety violation in customer_reply
_CREDENTIAL_REQUEST = re.compile(
    r"\b(share|provide|send|give|enter|type|confirm).{0,40}(pin|otp|one.?time.?pass|password|passcode|full.?card|card.?number)\b",
    re.IGNORECASE,
)
_CREDENTIAL_REQUEST_2 = re.compile(
    r"\b(pin|otp|password|passcode).{0,30}(share|provide|send|give|enter|confirm)\b",
    re.IGNORECASE,
)
_UNAUTHORIZED_REFUND = re.compile(
    r"\bwe (will|shall|are going to|are going) (refund|reverse|return|credit|reimburse)\b",
    re.IGNORECASE,
)
_UNAUTHORIZED_REFUND_2 = re.compile(
    r"\byou (will|shall) (receive|get).{0,20}refund\b",
    re.IGNORECASE,
)

_SAFE_REFUND_LANGUAGE = (
    "Any eligible amount will be returned through official channels."
)
_SAFE_CREDENTIAL_SUFFIX = (
    " Please do not share your PIN or OTP with anyone."
)


def check_and_fix(customer_reply: str) -> tuple[str, list[str]]:
    """Return (fixed_reply, list_of_violations_found)."""
    violations = []
    reply = customer_reply

    if _CREDENTIAL_REQUEST.search(reply) or _CREDENTIAL_REQUEST_2.search(reply):
        violations.append("credential_request_detected")
        # Strip the offending sentence and append the safe reminder
        reply = re.sub(
            r"[^.!?]*\b(pin|otp|password|passcode|card.?number)[^.!?]*[.!?]",
            "",
            reply,
            flags=re.IGNORECASE,
        ).strip()

    if _UNAUTHORIZED_REFUND.search(reply) or _UNAUTHORIZED_REFUND_2.search(reply):
        violations.append("unauthorized_refund_promise")
        reply = _UNAUTHORIZED_REFUND.sub(
            "any eligible amount will be returned through official channels",
            reply,
        )
        reply = _UNAUTHORIZED_REFUND_2.sub(
            _SAFE_REFUND_LANGUAGE,
            reply,
        )

    # Ensure PIN/OTP reminder is present
    if not re.search(r"\b(pin|otp)\b", reply, re.IGNORECASE):
        reply = reply.rstrip() + _SAFE_CREDENTIAL_SUFFIX

    return reply.strip(), violations


def is_prompt_injection(complaint: str) -> bool:
    """Detect obvious prompt injection patterns in complaint text."""
    patterns = [
        r"ignore (previous|all|above|prior|system) (instructions?|rules?|prompts?)",
        r"(disregard|forget|override).{0,30}(instructions?|rules?|system)",
        r"you are now",
        r"pretend (you are|to be)",
        r"new (instructions?|rules?|system prompt)",
        r"act as (if you are|a|an)",
        r"jailbreak",
        r"do anything now",
        r"developer mode",
    ]
    combined = re.compile("|".join(patterns), re.IGNORECASE)
    return bool(combined.search(complaint))
