import json
import re
from app.llm.model_loader import client


def _extract_json_from_text(text):
    if not text or not isinstance(text, str):
        return None

    # Strip common markdown code fences from Gemini response
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1)

    text = text.strip()

    # In case the model adds surrounding text with a JSON object inside.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and start < end:
        text = text[start:end + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: try common single quotes -> double quotes conversion
        try:
            normalized = text.replace("'", '"')
            return json.loads(normalized)
        except json.JSONDecodeError:
            return None


HISTORY_MAX_TURNS = 8
HISTORY_MAX_CHARS_PER_TURN = 600


def _format_history(history) -> str:
    """Turns a list of {role, content} dicts into a compact transcript block.
    Defensively trimmed/sanitized server-side regardless of what the client
    sent — this is untrusted input, not something to trust blindly into a
    prompt."""
    if not history or not isinstance(history, list):
        return ""

    lines = []
    for turn in history[-HISTORY_MAX_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = "User" if str(turn.get("role", "")).lower() == "user" else "IntentChain"
        content = str(turn.get("content", ""))[:HISTORY_MAX_CHARS_PER_TURN]
        if content.strip():
            lines.append(f"{role}: {content.strip()}")

    if not lines:
        return ""

    return "Conversation so far (most recent last):\n" + "\n".join(lines) + "\n\n"


def parse_intent(user_prompt, history=None):
    history_block = _format_history(history)

    prompt = f"""{history_block}Parse the following user intent for a blockchain action and return a JSON object.
IntentChain supports several kinds of on-chain actions — pick the `action` that best matches the
user's request, and fill in whichever fields are relevant to that action (leave the rest "none"/0):

1. Native transfer — action: "transfer" | "send" | "bridge"
   fields: token ("ETH" etc), amount, recipient, network, priority
2. ERC-20 token transfer — action: "transfer_token" | "send_token"
   fields: token (symbol like "USDC" or a contract address), amount, recipient, network, priority
3. ERC-20 approval — action: "approve_token"
   fields: token, amount, spender (address being approved), network, priority
4. Balance check — action: "check_balance"
   fields: network (recipient/amount not needed)
5. Transaction history lookup — action: "get_history"
   fields: network
6. Supply-chain / pharma traceability — register a new product batch — action: "register_product"
   fields: product_id (a batch/SKU string like "COFFEE-BATCH-A123"), name, origin, network
7. Supply-chain checkpoint / cold-chain logging — action: "log_checkpoint"
   fields: product_id, location, status (e.g. "In Transit", "Delivered"), temperature_c (integer, 0 if not mentioned), network
8. Product verification — action: "verify_product"
   fields: product_id, network
9. Token swap — action: "swap" (not yet executable, but still parse it)
   fields: token, amount, network
10. General blockchain/crypto knowledge question — the user is asking to learn or understand
    something (e.g. "what is a blockchain supply network", "how does gas work", "what's the
    difference between a wallet and a contract") rather than asking to do something with their
    own wallet — action: "general_question"
    fields: none of the transaction fields matter for this. Instead, answer the question yourself
    in the "answer" field: 2-5 sentences, clear and accurate, no jargon left unexplained. If the
    question is ambiguous between "explain a concept" and "do something in my wallet", prefer
    the wallet action only if it clearly references the user's own funds/address/history.
11. Deploy a private supply-chain contract — the user wants their own instance of the
    supply-chain traceability contract deployed and registered (e.g. "deploy my own supply
    chain contract", "set up my private traceability contract", "I need my own contract for
    tracking products") — action: "deploy_contract"
    fields: network

Return a single JSON object with exactly these keys:
- action
- token
- amount (number)
- recipient
- spender
- network (e.g. "ethereum", "sepolia", "polygon", "arbitrum", "optimism", "bsc") or "none"
- priority ("low", "normal", "high")
- gas_mode ("fastest", "standard", "cheapest") if the user explicitly asks to optimize for speed or cost, else "none"
- product_id
- name
- origin
- location
- status
- temperature_c (number)
- answer (only meaningful when action is "general_question" — your actual answer to the question, otherwise "none")

If a "Conversation so far" section is included above, use it only to understand what the user is
referring to conversationally (e.g. "it", "that", "the same one" pointing at something discussed
earlier) — this matters most for general_question answers, so follow-ups stay coherent. Never pull
amount, recipient, spender, token, or any other transaction field from earlier turns: those must
always come from the CURRENT message only, even if it means leaving a field "none" for the missing-
fields flow to ask about. Money-moving fields are never inferred from context.

User intent: "{user_prompt}"

Return only the JSON object, no additional text."""

    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)

    # Detect text payload in a few possible field patterns
    raw_text = None
    if hasattr(response, "text") and response.text:
        raw_text = response.text
    elif isinstance(response, dict):
        raw_text = response.get("text") or response.get("response")
        if not raw_text and "candidates" in response:
            raw_text = response["candidates"][0].get("content", {}).get("parts", [None])[0]
    else:
        # Some SDK responses put text in .candidates[...] structure
        candidates = getattr(response, "candidates", None)
        if candidates and len(candidates) > 0:
            part = candidates[0].get("content", {}).get("parts", [None])[0]
            raw_text = part

    parsed = _extract_json_from_text(raw_text)

    if parsed is None:
        raise ValueError(f"Could not parse intent JSON from model output: {raw_text}")

    # Ensure all required keys exist with safe defaults
    normalized = {
        "action": parsed.get("action", "none"),
        "token": parsed.get("token", "none"),
        "amount": parsed.get("amount", 0),
        "recipient": parsed.get("recipient", "none"),
        "spender": parsed.get("spender", "none"),
        "network": parsed.get("network", "none"),
        "priority": parsed.get("priority", "normal"),
        "gas_mode": parsed.get("gas_mode", "none"),
        "product_id": parsed.get("product_id", "none"),
        "name": parsed.get("name", "none"),
        "origin": parsed.get("origin", "none"),
        "location": parsed.get("location", "none"),
        "status": parsed.get("status", "none"),
        "temperature_c": parsed.get("temperature_c", 0),
        "answer": parsed.get("answer", "none"),
    }

    return normalized