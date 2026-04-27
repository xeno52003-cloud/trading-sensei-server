# 🥷 Trading Sensei - Webhook Server
# Docker configuration for easy deployment

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY webhook_server.py state_store.py app_state.py analytics.py users.py \
     oanda_client.py oanda_poller.py trade_history.py telegram_bot.py ./
COPY static ./static

# SQLite ledger lives here — mount a volume in production.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# Environment
ENV PORT=5000
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || exit 1

# Run with gunicorn for production
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:5000", "webhook_server:app"]
