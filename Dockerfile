FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/srv/.venv/bin:$PATH"

WORKDIR /srv

# Dependencies first so code edits do not invalidate the layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY apps ./apps
COPY main.py ./

RUN useradd --create-home --uid 1001 service && chown -R service:service /srv
USER service

EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
