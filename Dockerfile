FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
# digest-pinned (multi-arch index); dependabot bumps the tag+digest pair

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# no git in the image: self-update no-ops cleanly, and a container should be
# replaced by pulling a new image, not mutated.
RUN useradd --create-home --shell /bin/bash thunder

# requirements.lock = hash-pinned FULL graph (uv export of uv.lock); plain
# direct pins would resolve fresh, unpinned transitives at every image build.
COPY requirements.lock .

# no `pip install --upgrade pip`: it would be the sole unhashed install;
# 3.13-slim ships a pip that fully supports --require-hashes
#
# pyaes (transitive, via pymediainfo-pyrofork) ships an sdist and NO wheel. That
# is fine here only because it is pure Python -- no C extension, no ctypes, no
# cffi -- so pip builds a wheel from the sdist with no compiler. This image is
# slim on purpose and installs no build tooling, so if pyaes ever gains a native
# extension, or if anyone adds `--only-binary=:all:` or `--no-build-isolation`,
# this line fails with a confusing error rather than an obvious one.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY --chown=thunder:thunder . .

RUN mkdir -p /app Thunder/logs && chown -R thunder:thunder /app

# run as non-root
USER thunder

# container health follows /health. PORT is read the same way vars.py reads it:
# a malformed value must fail the probe AND the boot, not report healthy while
# the app refuses to start. The split('#') that used to be here made the probe
# accept something the app rejects.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080').strip()+'/health',timeout=5)"

CMD ["bash", "thunder.sh"]
