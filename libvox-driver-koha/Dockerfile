# The container form of the driver. The serverless version will build from the
# same source tree, swapping this entry point for the platform's.
FROM python:3.12-slim

# Dependencies first, so editing source does not invalidate the layer.
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps -e .

# The driver reaches someone else's library server and writes nothing locally;
# there is no reason for it to be able to write anything at all. An unprivileged
# user is the cheapest limit available on what a compromised dependency can do.
RUN useradd --create-home --uid 10001 driver
USER driver

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "libvox_driver_koha"]
CMD ["--help"]
