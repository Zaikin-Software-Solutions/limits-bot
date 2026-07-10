FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./

RUN pip install \
    "aiogram>=3.27,<4" \
    "apscheduler>=3.10,<4" \
    "httpx>=0.27" \
    "python-dotenv>=1.0" \
    "pydantic>=2.7" \
    "pydantic-settings>=2.4"

COPY app ./app

VOLUME ["/data"]

CMD ["python", "-m", "app.main"]
