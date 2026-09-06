FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# no git in the image: self-update no-ops cleanly without .git/git, and a
# container should be replaced by pulling a new image, not mutating itself.
# python:3.13-slim already ships everything else the runtime needs.
RUN useradd --create-home --shell /bin/bash thunder

# hash-pinned FULL graph (uv export of uv.lock) -- the previous direct-pins
# install resolved fresh, unpinned transitives on every build, so the
# supply-chain guarantees held in CI never reached the shipped image
COPY requirements.lock .

RUN pip install --upgrade pip && \
    pip install --no-cache-dir --require-hashes -r requirements.lock

COPY --chown=thunder:thunder . .

# L8: run as non-root
USER thunder

# L8: container health follows /health (M3); PORT may come from config.env
# (dotenv does not override pre-set env vars, so the check reads both)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import os,urllib.request; port=os.getenv('PORT','8080'); port=[l.split('=',1)[1].split('#',1)[0].strip().strip(chr(34)).strip(chr(39)) for l in (open('config.env').read().splitlines() if os.path.exists('config.env') else []) if l.strip().startswith('PORT') and '=' in l] or [port]; urllib.request.urlopen('http://127.0.0.1:'+port[0]+'/health', timeout=5)"

CMD ["bash", "thunder.sh"]
