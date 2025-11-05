import os
from openai import OpenAI


_client = None


def _get_client() -> OpenAI:
    """Obtém o cliente OpenAI, inicializando-o se necessário."""
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY não está configurada. "
                "Por favor, configure a variável de ambiente OPENAI_API_KEY."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def gpt_4_completion(prompt: str, max_tokens: int = 512) -> str:
    client = _get_client()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()


def gpt_3_5_completion(prompt: str, max_tokens: int = 512) -> str:
    client = _get_client()
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()

