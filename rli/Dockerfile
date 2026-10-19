FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/opt/harness-bench/bin:$PATH"

RUN python -m venv /opt/harness-bench
COPY rli/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY pyproject.toml README.md LICENSE /opt/harness-source/
COPY harness_bench /opt/harness-source/harness_bench
RUN pip install --no-cache-dir --no-deps /opt/harness-source \
    && rm -rf /opt/harness-source /tmp/requirements.txt

WORKDIR /workspace
CMD ["sleep", "infinity"]
