"""
Pure rule-based ticket investigator.
No external API calls. Deterministic. Zero latency overhead.
Used as primary engine; LLM is an optional enhancement layer.
"""
import re
from typing import Optional
from models import TicketRequest, TicketResponse, TransactionHistory

# ── Bangla numeral normalisation ────────────────────────────────────────────
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

def _normalise(text: str) -> str:
    return text.translate(_BN_DIGITS)


# ── Amount extraction ────────────────────────────────────────────────────────
_AMOUNT_RE = re.compile(
    r"(?:৳|BDT\s*)(\d[\d,]*)|\b(\d[\d,]*)\s*(?:taka|টাকা|bdt)\b",
    re.IGNORECASE,
)
_STANDALONE_RE = re.compile(r"\b(\d{3,6})\b")

def _extract_amounts(text: str) -> set[float]:
    text = _normalise(text)
    found: set[float] = set()
    for m in _AMOUNT_RE.finditer(text):
        val = (m.group(1) or m.group(2)).replace(",", "")
        found.add(float(val))
    for m in _STANDALONE_RE.finditer(text):
        found.add(float(m.group(1)))
    return found


# ── Case-type keyword patterns ───────────────────────────────────────────────
# Ordered by priority (first match wins in each complaint)
_CASE_PATTERNS: list[tuple[str, list[str]]] = [
    ("phishing_or_social_engineering", [
        r"\b(otp|one.?time.?pass(?:word)?|পিন|ওটিপি|pin)\b.{0,60}(ask|demand|want|request|share|দিতে|চাই|চাইছে)",
        r"(ask|request|demand|চাইছে|চেয়েছে).{0,60}\b(otp|pin|password|পাসওয়ার্ড|ওটিপি|পিন)\b",
        r"(someone|somebody|কেউ|লোক).{0,40}call.{0,40}(bkash|bank|company|অ্যাকাউন্ট)",
        r"account.{0,40}(block|suspend|close).{0,40}(otp|pin|password)",
        r"(fake|fraud|scam|ফিশিং|প্রতারণা)",
        r"(called|phoned|ফোন করেছে).{0,60}(otp|pin|password|ওটিপি|পিন)",
    ]),
    ("duplicate_payment", [
        r"(twice|two times|double|deducted.{0,20}twice|charged.{0,20}twice)",
        r"(দুইবার|দুবার|দ্বিগুণ|দুইটা)",
        r"same.{0,20}(payment|charge|deduction).{0,20}twice",
    ]),
    ("agent_cash_in_issue", [
        r"(agent|এজেন্ট).{0,60}(cash.?in|deposit|জমা|ক্যাশ)",
        r"(cash.?in|ক্যাশ.?ইন).{0,60}(agent|এজেন্ট)",
        r"(এজেন্ট).{0,60}(balance|ব্যালেন্স|আসেনি|পাইনি|দেখছি না)",
        r"(cash.?in|ক্যাশ.?ইন).{0,60}(balance|ব্যালেন্স|আসেনি)",
    ]),
    ("merchant_settlement_delay", [
        r"(settlement|সেটেলমেন্ট).{0,50}(delay|not.?received|pending|আসেনি|হয়নি)",
        r"(sales|payment|বিক্রয়).{0,50}(not.?settled|settle|সেটেল)",
        r"merchant.{0,40}settlement",
    ]),
    ("wrong_transfer", [
        r"wrong\s+(number|person|recipient|account|নম্বর|মানুষ)",
        r"(sent|transfer|পাঠিয়েছি).{0,40}(wrong|mistake|accidentally|ভুল|ভুলে)",
        r"(ভুল|ভুলে|ভুল করে).{0,40}(পাঠিয়েছি|পাঠিয়ে|নম্বর|মানুষ)",
        r"typed.{0,20}wrong",
        r"(accidentally|mistakenly).{0,30}(sent|transfer)",
        r"(wrong|mistake).{0,20}(sent|transfer)",
        r"(sent|transfer).{0,60}(didn'?t|did not|hasn'?t|has not).{0,20}(get|receive|got|received)",
        r"(sent|transfer).{0,60}(not received|never received|not get)",
        r"(brother|sister|friend|relative|bhai|ভাই|আপু|বন্ধু).{0,30}(didn'?t|did not|hasn'?t).{0,20}(get|receive)",
    ]),
    ("payment_failed", [
        r"(payment|transaction|recharge).{0,40}fail",
        r"fail.{0,40}(payment|transaction)",
        r"(showed?|shows?|said|display).{0,20}fail",
        r"balance.{0,30}deduct.{0,30}(but|yet|still|but).{0,20}fail",
        r"(ব্যর্থ|ফেইল)",
    ]),
    ("refund_request", [
        r"\b(refund|money.?back|reimburse|ফেরত|রিফান্ড)\b",
        r"(want|need|request|চাই|চাইছি).{0,30}(refund|money back|ফেরত)",
        r"(cancel|cancelled).{0,30}(refund|money back)",
        r"give.{0,20}(my|the).{0,10}money.{0,10}back",
    ]),
]


def _detect_case_type(complaint: str, history: list[TransactionHistory], user_type: Optional[str]) -> str:
    text = complaint.lower()

    for case_type, patterns in _CASE_PATTERNS:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return case_type

    # Transaction-type heuristics — only apply when complaint has relevant keywords
    complaint_lower = complaint.lower()
    mentions_money = bool(re.search(r"(cash|deposit|balance|agent|settlement|pay|transfer|recharge|bill)", complaint_lower))
    if mentions_money:
        for txn in history:
            if txn.type == "cash_in" and txn.counterparty.upper().startswith("AGENT-"):
                if re.search(r"(cash|agent|deposit|balance|received|আসেনি|এজেন্ট|ক্যাশ)", complaint_lower):
                    return "agent_cash_in_issue"
            if txn.type == "settlement" and txn.status == "pending":
                if re.search(r"(settlement|settle|sales|সেটেল)", complaint_lower):
                    return "merchant_settlement_delay"
            if txn.type == "payment" and txn.status == "failed":
                if re.search(r"(pay|fail|deduct|balance|bill|recharge)", complaint_lower):
                    return "payment_failed"

    if user_type == "merchant" and re.search(r"(settlement|sales|payment|not received)", complaint_lower):
        return "merchant_settlement_delay"

    return "other"


# ── Transaction matching ─────────────────────────────────────────────────────

def _same_day(ts1: str, ts2_prefix: str) -> bool:
    """Loose same-date check using ISO 8601 date prefix."""
    return ts1[:10] == ts2_prefix[:10]


def _find_transaction(
    complaint: str,
    history: list[TransactionHistory],
    case_type: str,
) -> tuple[Optional[str], str]:
    """Return (relevant_transaction_id | None, evidence_verdict)."""

    if not history:
        return None, "insufficient_data"

    if case_type in ("phishing_or_social_engineering", "other"):
        return None, "insufficient_data"

    amounts = _extract_amounts(_normalise(complaint))

    # ── Duplicate payment: find two identical txns ───────────────────────────
    if case_type == "duplicate_payment":
        seen: dict[tuple, list[TransactionHistory]] = {}
        for txn in history:
            key = (txn.amount, txn.counterparty, txn.type)
            seen.setdefault(key, []).append(txn)
        for key, txns in seen.items():
            if len(txns) >= 2:
                txns_sorted = sorted(txns, key=lambda t: t.timestamp)
                return txns_sorted[1].transaction_id, "consistent"
        # Fallback: any two payments of same amount
        payments = [t for t in history if t.type == "payment"]
        if len(payments) >= 2 and len({t.amount for t in payments}) == 1:
            payments_sorted = sorted(payments, key=lambda t: t.timestamp)
            return payments_sorted[1].transaction_id, "consistent"

    # ── Filter by type relevance ─────────────────────────────────────────────
    preferred_types = {
        "wrong_transfer": ["transfer"],
        "payment_failed": ["payment"],
        "agent_cash_in_issue": ["cash_in"],
        "merchant_settlement_delay": ["settlement"],
        "refund_request": ["payment"],
        "other": [],
    }
    pref = preferred_types.get(case_type, [])
    typed = [t for t in history if t.type in pref] if pref else history[:]

    # ── Filter by amount ─────────────────────────────────────────────────────
    amount_matched = [t for t in typed if amounts and t.amount in amounts]
    candidates = amount_matched if amount_matched else typed

    if not candidates:
        candidates = history[:]

    if not candidates:
        return None, "insufficient_data"

    # ── Ambiguity check for wrong_transfer ───────────────────────────────────
    if case_type == "wrong_transfer":
        # Check ALL amount-matching transfers (including failed), not just typed-filtered
        all_transfer_candidates = [t for t in history if t.type == "transfer"]
        if amounts:
            all_transfer_candidates = [t for t in all_transfer_candidates if t.amount in amounts] or all_transfer_candidates
        unique_counterparties = {t.counterparty for t in all_transfer_candidates}
        if len(unique_counterparties) > 1 and len(all_transfer_candidates) > 1:
            # Multiple transactions to DIFFERENT recipients — cannot determine which
            return None, "insufficient_data"
        # Use all_transfer_candidates instead of candidates from here
        candidates = all_transfer_candidates if all_transfer_candidates else candidates

        # Same-counterparty pattern = inconsistent
        picked = sorted(candidates, key=lambda t: t.timestamp)[-1]
        same_recipient_prior = [
            t for t in history
            if t.counterparty == picked.counterparty and t.transaction_id != picked.transaction_id
        ]
        if len(same_recipient_prior) >= 2:
            return picked.transaction_id, "inconsistent"
        return picked.transaction_id, "consistent"

    # ── General: pick most recent candidate ─────────────────────────────────
    picked = sorted(candidates, key=lambda t: t.timestamp)[-1]

    # Verify status is consistent with complaint
    verdict = "consistent"
    if case_type == "payment_failed" and picked.status == "completed":
        verdict = "inconsistent"
    if case_type == "agent_cash_in_issue" and picked.status == "completed":
        # Completed cash_in but customer claims not received — inconsistent
        verdict = "inconsistent"
    if case_type == "agent_cash_in_issue" and picked.status == "pending":
        verdict = "consistent"

    return picked.transaction_id, verdict


# ── Routing ──────────────────────────────────────────────────────────────────

_DEPT_MAP = {
    "wrong_transfer": "dispute_resolution",
    "payment_failed": "payments_ops",
    "refund_request": "customer_support",
    "duplicate_payment": "payments_ops",
    "merchant_settlement_delay": "merchant_operations",
    "agent_cash_in_issue": "agent_operations",
    "phishing_or_social_engineering": "fraud_risk",
    "other": "customer_support",
}

_SEVERITY_MAP = {
    "phishing_or_social_engineering": "critical",
    "wrong_transfer": "high",
    "payment_failed": "high",
    "duplicate_payment": "high",
    "agent_cash_in_issue": "high",
    "merchant_settlement_delay": "medium",
    "refund_request": "low",
    "other": "low",
}

_HUMAN_REVIEW = {
    "wrong_transfer", "duplicate_payment", "agent_cash_in_issue",
    "phishing_or_social_engineering",
}


def _severity(case_type: str, evidence_verdict: str, amount: Optional[float]) -> str:
    base = _SEVERITY_MAP.get(case_type, "low")
    # Ambiguous high-value case → bump to medium
    if evidence_verdict == "insufficient_data" and base == "high" and (amount is None or amount < 500):
        return "medium"
    # Low-value refund stays low
    return base


def _human_review(case_type: str, evidence_verdict: str) -> bool:
    if case_type in _HUMAN_REVIEW:
        return True
    if evidence_verdict == "inconsistent":
        return True
    return False


# ── Reply templates ──────────────────────────────────────────────────────────

_EN_TEMPLATES: dict[str, str] = {
    "wrong_transfer": (
        "We have noted your concern about transaction {txn_id}. "
        "Our dispute team will review the case and contact you through official support channels. "
        "Please do not share your PIN or OTP with anyone."
    ),
    "wrong_transfer_inconsistent": (
        "We have received your request regarding transaction {txn_id}. "
        "Our dispute team will review the case carefully and contact you through official support channels. "
        "Please do not share your PIN or OTP with anyone."
    ),
    "wrong_transfer_null": (
        "Thank you for reaching out. We see multiple transactions that could match your description. "
        "Could you share the recipient's phone number so we can identify the right transaction? "
        "Please do not share your PIN or OTP with anyone."
    ),
    "payment_failed": (
        "We have noted that transaction {txn_id} may have caused an unexpected balance deduction. "
        "Our payments team will review the case and any eligible amount will be returned through official channels. "
        "Please do not share your PIN or OTP with anyone."
    ),
    "refund_request": (
        "Thank you for reaching out. Refunds for completed merchant payments depend on the merchant's own policy. "
        "We recommend contacting the merchant directly. If you need assistance, please reply and we will guide you. "
        "Please do not share your PIN or OTP with anyone."
    ),
    "duplicate_payment": (
        "We have noted the possible duplicate payment for transaction {txn_id}. "
        "Our payments team will verify with the biller and any eligible amount will be returned through official channels. "
        "Please do not share your PIN or OTP with anyone."
    ),
    "merchant_settlement_delay": (
        "We have noted your concern about settlement {txn_id}. "
        "Our merchant operations team will check the batch status and update you on the expected settlement time through official channels."
    ),
    "agent_cash_in_issue": (
        "We have noted your concern about transaction {txn_id}. "
        "Our agent operations team will investigate and resolve this within the standard SLA. "
        "Please do not share your PIN or OTP with anyone."
    ),
    "phishing_or_social_engineering": (
        "Thank you for reaching out before sharing any information. "
        "We never ask for your PIN, OTP, or password under any circumstances. "
        "Please do not share these with anyone, even if they claim to be from us. "
        "Our fraud team has been notified of this incident."
    ),
    "other": (
        "Thank you for reaching out. To help you faster, please share the transaction ID, "
        "the amount involved, and a short description of what went wrong. "
        "Please do not share your PIN or OTP with anyone."
    ),
    "other_has_txn": (
        "Thank you for reaching out. We have noted your concern about transaction {txn_id}. "
        "Our support team will review and contact you through official channels. "
        "Please do not share your PIN or OTP with anyone."
    ),
}

_BN_TEMPLATES: dict[str, str] = {
    "wrong_transfer": (
        "আপনার লেনদেন {txn_id} এর বিষয়ে আমরা অবগত হয়েছি। "
        "আমাদের ডিসপিউট টিম বিষয়টি পর্যালোচনা করবে এবং অফিসিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "payment_failed": (
        "আমরা লক্ষ্য করেছি যে লেনদেন {txn_id} এ অপ্রত্যাশিত ব্যালেন্স কাটার ঘটনা ঘটতে পারে। "
        "আমাদের পেমেন্ট টিম বিষয়টি পর্যালোচনা করবে এবং যোগ্য পরিমাণ অফিসিয়াল চ্যানেলে ফেরত দেওয়া হবে। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "refund_request": (
        "আপনার সাথে যোগাযোগ করার জন্য ধন্যবাদ। মার্চেন্ট পেমেন্টের রিফান্ড মার্চেন্টের নিজস্ব নীতির উপর নির্ভর করে। "
        "সরাসরি মার্চেন্টের সাথে যোগাযোগ করার পরামর্শ দেওয়া হচ্ছে। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "duplicate_payment": (
        "লেনদেন {txn_id} এর সম্ভাব্য ডুপ্লিকেট পেমেন্টের বিষয়ে আমরা অবগত হয়েছি। "
        "আমাদের পেমেন্ট টিম বিলারের সাথে যাচাই করবে এবং যোগ্য পরিমাণ অফিসিয়াল চ্যানেলে ফেরত দেওয়া হবে। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "merchant_settlement_delay": (
        "সেটেলমেন্ট {txn_id} সম্পর্কে আপনার উদ্বেগ আমরা নোট করেছি। "
        "আমাদের মার্চেন্ট অপারেশন্স টিম ব্যাচ স্ট্যাটাস যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে।"
    ),
    "agent_cash_in_issue": (
        "আপনার লেনদেন {txn_id} এর বিষয়ে আমরা অবগত হয়েছি। "
        "আমাদের এজেন্ট অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "phishing_or_social_engineering": (
        "কোনো তথ্য শেয়ার করার আগে আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। "
        "আমরা কখনও আপনার পিন, ওটিপি বা পাসওয়ার্ড চাই না। "
        "এমনকি যদি কেউ আমাদের পক্ষ থেকে দাবি করে তাহলেও এগুলো কারো সাথে শেয়ার করবেন না। "
        "আমাদের ফ্রড টিমকে এই ঘটনার বিষয়ে অবহিত করা হয়েছে।"
    ),
    "other": (
        "আপনার সাথে যোগাযোগ করার জন্য ধন্যবাদ। আপনাকে দ্রুত সাহায্য করতে, "
        "অনুগ্রহ করে লেনদেন আইডি, পরিমাণ এবং সমস্যার সংক্ষিপ্ত বিবরণ শেয়ার করুন। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
}


def _build_reply(
    case_type: str,
    evidence_verdict: str,
    txn_id: Optional[str],
    language: Optional[str],
) -> str:
    use_bn = language == "bn"
    templates = _BN_TEMPLATES if use_bn else _EN_TEMPLATES

    # Pick template key
    key = case_type
    if not use_bn and case_type == "wrong_transfer" and evidence_verdict == "inconsistent":
        key = "wrong_transfer_inconsistent"
    elif not use_bn and case_type == "wrong_transfer" and txn_id is None:
        key = "wrong_transfer_null"
    elif not use_bn and case_type == "other" and txn_id:
        key = "other_has_txn"

    template = templates.get(key, templates.get("other", ""))
    placeholder = txn_id or "your transaction"
    return template.replace("{txn_id}", placeholder)


# ── Summary + next action ────────────────────────────────────────────────────

def _build_summary(
    case_type: str,
    evidence_verdict: str,
    txn_id: Optional[str],
    complaint: str,
    history: list[TransactionHistory],
) -> tuple[str, str]:
    """Return (agent_summary, recommended_next_action)."""

    txn_ref = txn_id or "an unidentified transaction"
    amount_str = ""
    if txn_id:
        matched = next((t for t in history if t.transaction_id == txn_id), None)
        if matched:
            amount_str = f"{int(matched.amount)} BDT "

    summaries = {
        "wrong_transfer": (
            f"Customer reports a {amount_str}wrong transfer via {txn_ref}. "
            + ("Evidence is consistent with the complaint." if evidence_verdict == "consistent"
               else "Transaction history shows a repeated pattern with the same recipient, suggesting an established contact." if evidence_verdict == "inconsistent"
               else "Multiple matching transactions found; recipient cannot be determined without further info."),
            "Initiate wrong-transfer dispute workflow. Verify recipient details with the customer before proceeding."
            if evidence_verdict == "consistent"
            else "Flag for human review. Verify with the customer whether this was genuinely a wrong transfer given the transaction pattern."
            if evidence_verdict == "inconsistent"
            else "Ask the customer for the recipient's phone number to identify the correct transaction.",
        ),
        "payment_failed": (
            f"Customer reports a {amount_str}payment ({txn_ref}) failed but balance may have been deducted.",
            f"Investigate {txn_ref} ledger status. If balance was deducted on a failed payment, initiate the automatic reversal flow within standard SLA.",
        ),
        "refund_request": (
            f"Customer requests a refund of {amount_str}for {txn_ref} (merchant payment). Not a service failure — change of mind.",
            "Inform the customer that refund eligibility depends on the merchant's policy. Guide them to contact the merchant directly.",
        ),
        "duplicate_payment": (
            f"Customer reports duplicate payment. Two identical {amount_str}transactions found in history; {txn_ref} is likely the duplicate.",
            f"Verify the duplicate with payments_ops. If the biller confirms only one payment was received, initiate reversal of {txn_ref}.",
        ),
        "merchant_settlement_delay": (
            f"Merchant reports {amount_str}settlement ({txn_ref}) delayed beyond the expected window. Settlement status is pending.",
            "Route to merchant_operations to verify settlement batch status. Communicate a revised ETA to the merchant if the batch is delayed.",
        ),
        "agent_cash_in_issue": (
            f"Customer reports {amount_str}cash-in via agent ({txn_ref}) not reflected in balance. Transaction status is pending.",
            f"Investigate {txn_ref} pending status with agent operations. Confirm settlement state and resolve within the standard cash-in SLA.",
        ),
        "phishing_or_social_engineering": (
            "Customer reports an unsolicited call/message claiming to be from the platform and requesting credentials. Customer has not yet shared any credentials.",
            "Escalate to fraud_risk team immediately. Confirm to customer that the platform never asks for OTP/PIN. Log reported contact details for fraud pattern analysis.",
        ),
        "other": (
            "Customer reports a vague concern without specifying the transaction, amount, or nature of the issue.",
            "Reply to customer asking for specific details: transaction ID, amount, and description of what went wrong.",
        ),
    }

    return summaries.get(case_type, summaries["other"])


# ── Reason codes ─────────────────────────────────────────────────────────────

def _reason_codes(case_type: str, evidence_verdict: str, txn_id: Optional[str]) -> list[str]:
    codes = [case_type]
    if txn_id:
        codes.append("transaction_match")
    if evidence_verdict == "inconsistent":
        codes.append("evidence_inconsistent")
    if evidence_verdict == "insufficient_data":
        codes.append("needs_clarification" if txn_id is None else "insufficient_data")
    codes.append(f"verdict_{evidence_verdict}")
    return codes


# ── Main entry point ─────────────────────────────────────────────────────────

def analyze_ticket_rules(req: TicketRequest) -> TicketResponse:
    history = req.transaction_history or []
    complaint = req.complaint or ""
    language = req.language

    # Auto-detect Bangla if not provided
    if not language:
        bangla_chars = len(re.findall(r"[ঀ-৿]", complaint))
        if bangla_chars > 5:
            language = "bn"
        else:
            language = "en"

    case_type = _detect_case_type(complaint, history, req.user_type)
    txn_id, evidence_verdict = _find_transaction(complaint, history, case_type)

    # Amount for severity
    matched_txn = next((t for t in history if t.transaction_id == txn_id), None)
    amount = matched_txn.amount if matched_txn else None

    severity = _severity(case_type, evidence_verdict, amount)
    department = _DEPT_MAP[case_type]
    human_review = _human_review(case_type, evidence_verdict)
    customer_reply = _build_reply(case_type, evidence_verdict, txn_id, language)
    agent_summary, next_action = _build_summary(case_type, evidence_verdict, txn_id, complaint, history)
    reason_codes = _reason_codes(case_type, evidence_verdict, txn_id)

    return TicketResponse(
        ticket_id=req.ticket_id,
        relevant_transaction_id=txn_id,
        evidence_verdict=evidence_verdict,
        case_type=case_type,
        severity=severity,
        department=department,
        agent_summary=agent_summary,
        recommended_next_action=next_action,
        customer_reply=customer_reply,
        human_review_required=human_review,
        confidence=0.80,
        reason_codes=reason_codes,
    )
