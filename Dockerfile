# Warden 应用镜像（个人自托管场景：单阶段、够用就好）。
# 构建：docker build -t warden:local .
# 一把起全栈：cd deploy && docker compose up -d --build
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 依赖与代码一起装（个人项目优先简单；改代码重建即可）
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install --no-cache-dir .

# 运行时目录（配合 compose 的命名卷持久化）
RUN mkdir -p /app/data /app/reports \
    && useradd --create-home --uid 10001 warden \
    && chown -R warden:warden /app
USER warden

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
