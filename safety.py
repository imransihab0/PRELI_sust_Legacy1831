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

    # Ensure PIN/OTP reminder is present (check both English and Bangla)
    has_reminder = re.search(r"\b(pin|otp)\b", reply, re.IGNORECASE) or \
                   re.search(r"(পিন|ওটিপি)", reply)
    if not has_reminder:
        reply = reply.rstrip() + _SAFE_CREDENTIAL_SUFFIX

    return reply.strip(), violations


_INJECTION_PATTERNS = re.compile(
    "|".join([
        # Classic ignore-rules patterns
        r"ignore (previous|all|above|prior|system|the) (instructions?|rules?|prompts?|constraints?)",
        r"(disregard|forget|override|bypass|skip).{0,30}(instructions?|rules?|system|constraints?|safety)",
        r"do not follow (previous|the|any|your) (instructions?|rules?)",
        # Role/persona hijacking
        r"you are now\b",
        r"pretend (you are|to be|that you)",
        r"act as (if you are|a |an |though)",
        r"(roleplay|role.play) as",
        r"from now on (you are|act|behave|respond)",
        r"your new (role|persona|instructions?|task)",
        # System prompt injection
        r"new (instructions?|rules?|system prompt|task|directive)",
        r"\[?system\]?:?\s*(new|updated|override)",
        r"<(system|instructions?|prompt)>",
        # DAN / jailbreak
        r"jailbreak",
        r"do anything now",
        r"developer mode",
        r"dan mode",
        r"unrestricted mode",
        r"(enable|activate|turn on).{0,20}(unrestricted|jailbreak|dan)",
        # Output manipulation
        r"(respond|reply|answer|output).{0,30}(without|ignore|skip).{0,30}(safety|filter|rule|restriction)",
        r"(confirm|approve|process).{0,20}(refund|reversal|transfer).{0,20}(immediately|now|directly)",
        r"tell (the customer|user|them).{0,30}(refund|reversed|approved|confirmed)",
        # Hidden instruction markers
        r"---.{0,10}(begin|start|end|stop).{0,10}---",
        r"\[\[.{0,50}\]\]",
    ]),
    re.IGNORECASE,
)


def is_prompt_injection(complaint: str) -> bool:
    """Detect prompt injection attempts embedded in complaint text."""
    return bool(_INJECTION_PATTERNS.search(complaint))
