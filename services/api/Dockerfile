FROM python:3.13-slim-bookworm AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build

RUN python -m venv "$VIRTUAL_ENV"

COPY pyproject.toml ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir ".[azure]"

FROM python:3.13-slim-bookworm AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build tooling is not needed at run time; removing it also removes its vulnerabilities.
RUN python -m pip uninstall -y setuptools wheel 2>/dev/null; python -m pip install --no-cache-dir --upgrade pip

RUN groupadd --gid 10001 nodo \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin nodo

COPY --from=builder /opt/venv /opt/venv

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "nodo_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
