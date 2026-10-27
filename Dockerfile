# Образ приложения TeamPlan (ТП-3). Запускается вместе с PostgreSQL через docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Приложение работает от непривилегированного пользователя. Данные хранятся
# в PostgreSQL (контейнер db), адрес базы задаётся переменной DATABASE_URL (ТР-7).
RUN useradd --create-home --uid 1000 teamplan
USER teamplan

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"

# startup.py готовит базу по режиму TEAMPLAN_MODE (ТР-6): в демонстрационном режиме
# seed.py создаёт тестовые данные только в пустой базе, существующие не трогает;
# в рабочем режиме тестовые данные не создаются (РЗ-7).
CMD ["sh", "-c", "python startup.py && exec streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true"]
