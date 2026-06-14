FROM python:3.12-alpine

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . .

RUN uv sync --frozen --no-dev --compile-bytecode

EXPOSE 8080

CMD ["uv", "run", "python", "-m", "src"]
