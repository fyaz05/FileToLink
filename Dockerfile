FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Prebuilt wheels (see requirements.txt); no build toolchain needed.
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=10001:10001 . .

USER 10001:10001

EXPOSE 8080

# ENTRYPOINT forwards --check; update.py skips without git.
ENTRYPOINT ["bash", "thunder.sh"]
CMD []
