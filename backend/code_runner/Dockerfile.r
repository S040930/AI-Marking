FROM rocker/r-ver:4.4.3
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
COPY case_runner.py /runner/case_runner.py
ENTRYPOINT []
