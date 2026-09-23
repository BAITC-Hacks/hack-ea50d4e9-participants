import os


DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://careerquest:careerquest@localhost:5432/careerquest"
)
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto").lower()
AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "7.5"))
