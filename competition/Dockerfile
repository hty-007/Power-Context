FROM quay.io/sclorg/python-312-c9s@sha256:1185c4593a4f739d06cb0d4f9d34cbf11e1792b0c0ca708078bb4dcd430bd862

USER 0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/selftest-requirements.txt
RUN dnf -y install iproute && dnf clean all
RUN python3 -m venv /opt/selftest-venv \
    && /opt/selftest-venv/bin/pip install --no-cache-dir -r /tmp/selftest-requirements.txt
ENV PATH="/opt/selftest-venv/bin:$PATH"
WORKDIR /tmp
COPY selftest-entrypoint.sh /usr/local/bin/selftest-entrypoint
ENTRYPOINT ["/bin/sh", "/usr/local/bin/selftest-entrypoint"]
CMD ["make", "-C", "/selftest", "test", "PYTHON=python", "PRODUCT=/candidate"]
