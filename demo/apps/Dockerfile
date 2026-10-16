FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi uvicorn python-multipart
WORKDIR /app
COPY . /app
ENV APP=leavetrack PORT=3000
CMD ["sh", "-c", "uvicorn ${APP}:app --host 0.0.0.0 --port ${PORT}"]
