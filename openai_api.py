import os

from openai import AzureOpenAI, OpenAI

# Configuration from environment
endpoint = os.environ.get(
    "AZUREAI_ENDPOINT",
    "https://jason-m8zmz6za-eastus2.cognitiveservices.azure.com/",
)
deployment = os.environ.get("AZUREAI_DEPLOYMENT", "gpt-4o-mini")
subscription_key = os.environ.get("AZUREAI_API_KEY", "")
deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")


def azureAI(text: str):
    """Summarise a LinkedIn post using Azure OpenAI."""
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=subscription_key,
        api_version="2024-05-01-preview",
    )

    response = client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": """You are an AI assistant who is tasked to summarise a Linkedin post. 
The audience who will read your summary consists of busy business owners, so it must be succinct and easy to understand.
Your summary will need to be:
- written in the same language as the given post
- less than 480 characters or 75 words
- No emoji and hashtags""",
            },
            {
                "role": "user",
                "content": "This is the post content: " + text,
            },
        ],
        max_completion_tokens=800,
        temperature=0.2,
        top_p=1.0,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        model=deployment,
    )

    return response


def deepseekAI(text: str):
    """Summarise a LinkedIn post using DeepSeek via OpenAI-compatible API."""
    client_deepseek = OpenAI(
        api_key=deepseek_api_key,
        base_url="https://api.deepseek.com",
    )

    response = client_deepseek.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": """You are an AI assistant who is tasked to summarise a Linkedin post. 
The audience who will read your summary consists of busy business owners, so it must be succinct and easy to understand.
Your summary will need to be:
- written in the same language as the given post
- less than 480 characters or 75 words
- No emoji and hashtags""",
            },
            {
                "role": "user",
                "content": "This is the post content: " + text,
            },
        ],
        max_tokens=800,
        temperature=0.2,
        top_p=1.0,
        frequency_penalty=0.0,
        presence_penalty=0.0,
    )

    return response

