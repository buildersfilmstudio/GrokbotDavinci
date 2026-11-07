FROM python:3.13-slim
RUN useradd --create-home --uid 10001 relay
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir /state && chown relay:relay /state
USER relay
ENV DAVINCI_DB=/state/relay.sqlite3
EXPOSE 8000
# Only the relay is hosted. Use trusted managed HTTPS ingress; never publish Resolve.
CMD ["uvicorn", "grokbot_davinci.relay:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
