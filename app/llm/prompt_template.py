def build_prompts(user_prompt):
    return {
        "action": f"What is the action in this sentence? Sentence: {user_prompt} Answer:",
        "token": f"What is the cryptocurrency token in this sentence? Sentence: {user_prompt} Answer:",
        "amount": f"What is the numeric amount in this sentence? Sentence: {user_prompt} Answer:",
        "recipient": f"What is the recipient address in this sentence? If none, say none. Sentence: {user_prompt} Answer:",
        "network": f"What is the blockchain network in this sentence? If none, say none. Sentence: {user_prompt} Answer:",
        "priority": f"What is the transaction priority in this sentence? If none, say normal. Sentence: {user_prompt} Answer:",
    }