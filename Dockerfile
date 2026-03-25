FROM python:3.11-slim

WORKDIR /app

ARG APP_VERSION=dev
ENV PYTHONUNBUFFERED=1
ENV APP_VERSION=${APP_VERSION}
ENV TZ=UTC
ENV PUID=1000
ENV PGID=1000

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata gosu tini \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 appgroup \
 && useradd -u 1000 -g 1000 -M -d /nonexistent -s /usr/sbin/nologin appuser

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py /app/app.py
COPY logging_setup.py /app/logging_setup.py
COPY scheduler.py /app/scheduler.py
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
CMD ["python", "/app/scheduler.py"]
