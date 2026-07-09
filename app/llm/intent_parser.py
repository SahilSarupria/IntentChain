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


def parse_intent(user_prompt):
    prompt = f"""Parse the following user intent for a blockchain action and return a JSON object.
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
    }

    return normalized
