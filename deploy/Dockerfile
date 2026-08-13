FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRADINGOS_CONFIG_PATH=/runtime/config/security_config.json \
    TRADINGOS_STATE_DIR=/runtime/state

WORKDIR /app
RUN groupadd --system tradingos && useradd --system --gid tradingos --home /app tradingos \
    && mkdir -p /runtime/config /runtime/state /runtime/jobs \
    && chown -R tradingos:tradingos /app /runtime

COPY --chown=tradingos:tradingos tools/ /app/tools/
COPY --chown=tradingos:tradingos configs/ /app/configs/
COPY --chown=tradingos:tradingos deploy/ /app/deploy/

USER tradingos
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python /app/tools/tradingos_runtime_service.py health --config "$TRADINGOS_CONFIG_PATH" >/dev/null || exit 1

CMD ["python", "/app/tools/tradingos_runtime_service.py", "serve", "--config", "/runtime/config/security_config.json", "--bind", "0.0.0.0", "--port", "8787"]
