# TeamPlan в Docker (ТП-3, ТП-4).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TEAMPLAN_DB=/data/teamplan.db

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Папка базы принадлежит непривилегированному пользователю; именованный том
# при первом подключении получает те же права.
RUN useradd --create-home --uid 1000 teamplan \
    && mkdir -p /data \
    && chown teamplan:teamplan /data
USER teamplan

VOLUME ["/data"]
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"

# seed.py создаёт тестовые данные только в пустой базе, существующие не трогает.
CMD ["sh", "-c", "python seed.py && exec streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true"]
