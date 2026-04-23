# ─────────────────────────────────────────────────────────────────────────────
# TCC-CONSOLIDACION-INFORMES — Backend Dockerfile
# Imagen de producción: Python 3.12 slim
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# Dependencias del sistema (playwright requiere chromium en modo scraping)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    # Fuentes para ReportLab PDF
    fonts-liberation \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─── Dependencias Python ──────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Instalar browsers de playwright (solo si se usa modo scraping)
RUN playwright install chromium --with-deps || true

# ─── Código fuente ────────────────────────────────────────────────────────────
COPY . .

# Directorio de reportes generados (montable como volumen)
RUN mkdir -p /app/reports/diario /app/reports/semanal

# ─── Variables de entorno base ────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    REPORTS_OUTPUT_DIR=/app/reports

# ─── Usuario no-root ─────────────────────────────────────────────────────────
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

# ─── Health check ─────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
