FROM python:3.8-slim
WORKDIR /usr/src/app
COPY . .
RUN apt update -y && apt install jq -y
RUN pip install --no-cache-dir -r requirements.txt
ADD https://github.com/grafana/mimir/releases/download/mimir-2.11.0/mimirtool-linux-amd64 /usr/local/bin/mimirtool
RUN chmod +x /usr/local/bin/mimirtool
RUN mkdir /tmp/prometheus-rules
EXPOSE 80
CMD ["python", "./optimizer.py"]