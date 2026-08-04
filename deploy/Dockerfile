FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# docker CLI(只有 client):bot 內建排程要開關 ytm-firefox 容器。
# 用官方 static binary,不裝 docker.io(那包含整個 daemon)。
RUN apt-get update && apt-get install -y --no-install-recommends curl tzdata \
    && arch="$(uname -m)" \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${arch}/docker-27.5.1.tgz" \
       | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# 程式碼打包進 image，這樣不掛載也跑得起來（否則錯誤訊息是難懂的
# ModuleNotFoundError: No module named 'ytm'）。
# 執行時用 -v 掛載 ytm/ 會覆蓋這份，所以改 script 仍然不用重 build。
COPY ytm/ ./ytm/

# data/(pool.json、browser.json、bot_config.json…)必須掛載進來，
# 它們是機密與執行時資料，不進 image。
CMD ["python", "-m", "ytm.telegram_bot"]
