FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 程式碼打包進 image，這樣不掛載也跑得起來（否則錯誤訊息是難懂的
# ModuleNotFoundError: No module named 'ytm'）。
# 執行時用 -v 掛載 ytm/ 會覆蓋這份，所以改 script 仍然不用重 build。
COPY ytm/ ./ytm/

# data/(pool.json、browser.json、bot_config.json…)必須掛載進來，
# 它們是機密與執行時資料，不進 image。
CMD ["python", "-m", "ytm.daily_pick", "--count", "20"]
