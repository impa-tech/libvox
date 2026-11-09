FROM python:3.12-slim

WORKDIR /app

# **The build context is this directory**, unlike /libvox-sync's, which is built
# from the repository root because it imports /libvox-driver-koha.
#
# This module does not, on the path that runs here. The Koha driver reaches it
# only through `direct_koha.py`, whose import sits inside a function in
# server.py and fires solely for LIBVOX_MCP_BACKEND=koha -- the temporary
# experiment. With the `core` backend nothing loads it, so the image does not
# carry it and the context stays one directory.
#
# The consequence, said plainly rather than discovered: this image cannot run
# the direct-Koha backend. That experiment stays a host-side thing, which is
# what its own README already calls it.

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e .

# Not root. This process terminates a bearer token on a published port, and it
# has no reason to be able to write anywhere in the image.
RUN useradd --create-home --uid 10001 libvox && chown -R libvox /app
USER libvox

ENV PYTHONUNBUFFERED=1

# streamable-http, never stdio: authorization rides HTTP headers, and on stdio
# every caller is anonymous -- so every tool call is refused for want of a
# tenant. A stdio server in a container would start cleanly and answer nothing.
ENV LIBVOX_MCP_TRANSPORT=streamable-http \
    LIBVOX_MCP_HOST=0.0.0.0 \
    LIBVOX_MCP_PORT=8000

EXPOSE 8000

ENTRYPOINT ["python", "-m", "libvox_mcp"]
