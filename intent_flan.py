from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

model_name = "google/flan-t5-base"

tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForSeq2SeqLM.from_pretrained(model_name)

prompt = """
Convert the user request into JSON with fields:
action, token, amount, recipient, network, priority.

User request: Send 0.05 ETH to 0xabc quickly
"""

inputs = tokenizer(prompt, return_tensors="pt")
outputs = model.generate(**inputs, max_new_tokens=150)

response = tokenizer.decode(outputs[0], skip_special_tokens=True)
print(response)