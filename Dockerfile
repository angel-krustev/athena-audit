FROM python:3.13-slim

WORKDIR /app

COPY src/ ./
COPY bin/cihi_auth-*.whl /tmp/

# Install cihi_auth and dependencies if whl exists
RUN if ls /tmp/cihi_auth-*.whl 1>/dev/null 2>&1; then \
      pip install --no-cache-dir /tmp/cihi_auth-*.whl --no-deps && \
      pip install --no-cache-dir "urllib3>=2.6.3,<3" "click>=8.1.7,<8.2" "PyJWT>=2.8.0,<3" "chardet>=5.2.0,<6" "requests>=2.31.0,<3" && \
      rm -f /tmp/cihi_auth-*.whl; \
    fi

RUN pip install --no-cache-dir boto3

ENTRYPOINT ["python", "run_history.py"]
