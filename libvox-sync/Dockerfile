FROM python:3.12-slim

WORKDIR /app

# The build context is the repository ROOT, not this directory — see the
# `build:` block in docker-compose.yml and the root .dockerignore.
#
# Why: /libvox-sync imports /libvox-driver-koha, so that Koha knowledge lives
# in exactly one place, and a Docker build context cannot reach outside
# itself. The alternative — building a wheel of the driver beforehand — leaves
# `docker compose build sync` failing until someone remembers the extra step,
# and a build step you can forget is one you eventually will.

# The sibling driver first: it changes less often than this module's source,
# and it brings httpx2 with it.
COPY libvox-driver-koha ./libvox-driver-koha
RUN pip install --no-cache-dir -e ./libvox-driver-koha

# Editable, both of them, and deliberately: the migration runner locates
# `migrations/` relative to its own file, so a site-packages copy would look
# for the SQL in the wrong place.
COPY libvox-sync/pyproject.toml libvox-sync/README.md ./libvox-sync/
COPY libvox-sync/migrations ./libvox-sync/migrations
COPY libvox-sync/src ./libvox-sync/src
RUN pip install --no-cache-dir -e ./libvox-sync

# Runs as a non-root user: this container holds ILS credentials in its
# environment and should not be able to write outside its own tree.
RUN useradd --create-home --uid 10001 libvox && chown -R libvox:libvox /app
USER libvox

ENTRYPOINT ["python", "-m", "libvox_sync"]
CMD ["--help"]
