FROM python:3.15-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Prebuilt wheels (see requirements.txt); no build toolchain needed.
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# chown so the unprivileged user can write Thunder/logs and the session file at runtime.
COPY --chown=10001:10001 . .

USER 10001:10001

EXPOSE 8080

# thunder.sh tries update.py first; no git in the image means it is skipped and
# the bot runs the code baked in here. Arguments replace CMD, so ENTRYPOINT is
# what makes `docker run image --check` reach thunder.sh.
ENTRYPOINT ["bash", "thunder.sh"]
CMD []
