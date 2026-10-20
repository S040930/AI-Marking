FROM eclipse-temurin:21.0.7_6-jdk-jammy
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && ln -s /usr/bin/python3 /usr/local/bin/python3 \
    && rm -rf /var/lib/apt/lists/*
COPY case_runner.py /runner/case_runner.py
ENTRYPOINT []
