FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=60

WORKDIR /app
COPY . /app/
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]