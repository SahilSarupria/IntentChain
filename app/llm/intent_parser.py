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
    prompt = f"""Parse the following user intent for a cryptocurrency transaction and return a JSON object with the following fields:
- action: The action to perform (e.g., "send", "transfer", "swap")
- token: The cryptocurrency token (e.g., "ETH", "BTC", "USDC")
- amount: The numeric amount (e.g., 0.1, 100)
- recipient: The recipient address or "none" if not specified
- network: The blockchain network (e.g., "ethereum", "polygon", "bsc") or "none"
- priority: The transaction priority ("low", "normal", "high")

User intent: "{user_prompt}"

Return only the JSON object, no additional text."""

    response = client.models.generate_content(model="gemini-2.5-flash-lite", contents=prompt)

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
        "network": parsed.get("network", "none"),
        "priority": parsed.get("priority", "normal"),
    }

    return normalized
