FROM python:3.12.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.1
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY web ./web
COPY THIRD_PARTY_NOTICES.md ./
COPY licenses ./licenses
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8100 8101
