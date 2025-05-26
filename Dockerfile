FROM python:3.11-slim-bullseye

WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get upgrade -y && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY . .
ENV STREAMLIT_SERVER_HEADLESS=true \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# entrypoint definitivo lo fijaremos desde docker-compose
CMD ["sleep", "infinity"]
