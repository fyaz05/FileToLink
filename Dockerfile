FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# No build toolchain needed: dependencies provide prebuilt wheels (see requirements.txt).
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Owned by unprivileged user so Thunder/logs and session file can be written at runtime.
COPY --chown=10001:10001 . .

USER 10001:10001

EXPOSE 8080

# Start. thunder.sh tries update.py first; with no git or .git in the image that
# is reported as not applicable and the bot starts on the code baked in here.
CMD ["bash", "thunder.sh"]
