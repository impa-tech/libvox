FROM python:3.12-slim

WORKDIR /app

# The build context is the repository ROOT -- see docker-compose.yml. Core
# imports two siblings, and a Docker build context cannot reach outside itself.
# Same arrangement, and the same reason, as /libvox-sync.

COPY libvox-driver-koha ./libvox-driver-koha
COPY libvox-sync ./libvox-sync
RUN pip install --no-cache-dir -e ./libvox-driver-koha -e ./libvox-sync

COPY libvox-core/pyproject.toml libvox-core/README.md ./libvox-core/
COPY libvox-core/src ./libvox-core/src
RUN pip install --no-cache-dir -e ./libvox-core

# After the install, so editing a migration does not invalidate it. They are
# data rather than code -- `pip install -e` never needed them, and `migrate`
# locates them relative to the package, which is why they have to be here at
# all rather than only in the repository.
COPY libvox-core/migrations ./libvox-core/migrations

# Non-root: this container holds the key that decrypts every library's ILS
# credential, for the duration of one call each time.
RUN useradd --create-home --uid 10001 libvox && chown -R libvox:libvox /app
USER libvox

# 0.0.0.0 inside the container, because compose is what publishes the port --
# and it publishes to 127.0.0.1 only. The default outside a container stays
# loopback; see config.py.
ENV LIBVOX_CORE_HOST=0.0.0.0
EXPOSE 8091 8092

ENTRYPOINT ["python", "-m", "libvox_core"]
