from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

model_name = "microsoft/phi-3-mini-4k-instruct"

tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    dtype=torch.float16,  # or torch.float32 if no GPU
    device_map="auto",
    local_files_only=True
)