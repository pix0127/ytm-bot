FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ytm/(程式)與 data/(pool.json、browser.json…)在執行時用 -v 掛載進 /app，
# 所以改 script 不用重 build，只有改依賴才需要。
CMD ["python", "-m", "ytm.daily_pick", "--count", "20"]
