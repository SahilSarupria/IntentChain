import json
from app.llm.model_loader import tokenizer, model
from app.llm.prompt_template import build_prompts

def parse_intent(user_prompt):
    prompts = build_prompts(user_prompt)
    intent = {}

    for field, prompt in prompts.items():
        inputs = tokenizer(prompt, return_tensors="pt")
        outputs = model.generate(**inputs, max_new_tokens=20)
        response = tokenizer.decode(outputs[0], skip_special_tokens=True).strip()
        intent[field] = None if response.lower() in ("none", "", "n/a") else response

    return intent