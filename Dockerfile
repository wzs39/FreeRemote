# FreeRemote 中继服务器（relay.py）——用于部署到 Fly.io / Render 等平台。
# 只在容器里装 relay 需要的东西；电脑端依赖（pyautogui/mss 等）不进镜像。
FROM python:3.11-slim

WORKDIR /app

# 先拷贝依赖清单并安装，利于复用构建缓存
COPY requirements-relay.txt ./
RUN pip install --no-cache-dir -r requirements-relay.txt

# 拷贝 relay 代码与其服务的手机端网页（index.html / doctor.html）
COPY relay.py ./
COPY web ./web

# 日志实时输出到 stdout（平台日志可查）
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

# --xf-trusted：平台（Fly/Render/Caddy）在边缘终结 TLS，信任 X-Forwarded-For，
# 这样限速/审计/白名单拿到的是手机真实 IP 而非代理 IP。
CMD ["python", "relay.py", "--port", "8080", "--xf-trusted"]