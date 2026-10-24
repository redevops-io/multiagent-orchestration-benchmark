# The runner and the provider share one image. The base is pinned by digest; agentic-os is installed from a staged copy of the pinned revision.
# Some of agentic-os's dependencies are git tags (not digests), so the build stage needs git; the final image has neither git nor a compiler.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY agentic-os /opt/agentic-os
RUN pip install --no-cache-dir --prefix=/install /opt/agentic-os

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin runner
COPY --from=build /install /usr/local
COPY agent.py provider.py scenario.json scenario-2.json /app/
USER 10001
WORKDIR /app
