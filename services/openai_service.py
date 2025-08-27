import os
from openai import OpenAI


_api_key = os.getenv("OPENAI_API_KEY")
_client = OpenAI(api_key=_api_key) if _api_key else OpenAI()


def gpt_4_completion(prompt: str, max_tokens: int = 512) -> str:
    response = _client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()


def gpt_3_5_completion(prompt: str, max_tokens: int = 512) -> str:
    response = _client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()

