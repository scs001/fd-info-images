FROM python:3.12-slim

# 索引与正文 spool 都是持久卷；镜像不内置数据。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FD_INFO_DB=/data/fd_info.db \
    FD_INFO_SPOOL=/data/spool

WORKDIR /app
COPY pyproject.toml README.md ./
COPY fd_info ./fd_info
RUN pip install --no-cache-dir ".[server]"

RUN mkdir -p /data/spool && useradd -u 1000 -m app && chown -R app /data
USER app

EXPOSE 8080 8081
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=3).status==200 else 1)"

CMD ["fd-info-serve", "--http", "8080"]
