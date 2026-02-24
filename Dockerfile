FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY briefing /app/briefing
COPY templates /app/templates

# 컨테이너에서는 config/와 data/를 볼륨으로 마운트하는 것을 권장
ENTRYPOINT ["python", "-m", "briefing"]

