FROM python:3.13-slim-bookworm AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build

RUN python -m venv "$VIRTUAL_ENV"

COPY pyproject.toml ./
COPY src ./src

# Package tooling is not needed at run time, and its bundled libraries are flagged by scanners,
# so it is removed from the venv after the install.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir ".[azure]" \
    && rm -rf /opt/venv/lib/python3.13/site-packages/pip \
              /opt/venv/lib/python3.13/site-packages/pip-* \
              /opt/venv/lib/python3.13/site-packages/setuptools* \
              /opt/venv/lib/python3.13/site-packages/wheel* \
              /opt/venv/bin/pip*

FROM python:3.13-slim-bookworm AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The base image ships its own pip and setuptools; the application does not use them.
RUN python -m pip uninstall -y pip setuptools wheel

RUN groupadd --gid 10001 nodo \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin nodo

COPY --from=builder /opt/venv /opt/venv

USER 10001:10001

ENV WEB_CONCURRENCY=2
EXPOSE 8000

CMD ["python", "-m", "nodo_api.serve"]
