FROM python:3.8-slim
WORKDIR /usr/src/app
COPY . .
RUN apt update -y && apt install jq curl -y
RUN pip install --no-cache-dir -r requirements.txt
ADD https://github.com/grafana/mimir/releases/download/mimir-2.11.0/mimirtool-linux-amd64 /usr/local/bin/mimirtool
RUN curl -L https://github.com/google/yamlfmt/releases/download/v0.11.0/yamlfmt_0.11.0_Linux_x86_64.tar.gz -o /tmp/yamlfmt_0.11.0_Linux_x86_64.tar.gz ; tar xzf /tmp/yamlfmt_0.11.0_Darwin_arm64.tar.gz && mv /tmp/yamlfmt /usr/local/bin/yamlfmt
RUN chmod +x /usr/local/bin/yamlfmt
RUN chmod +x /usr/local/bin/mimirtool
EXPOSE 80
CMD ["python", "./optimizer.py"]