FROM postgres:18.7-trixie

ENV PATH="/opt/patroni/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/patroni \
    && pip install --no-cache-dir 'patroni[etcd3,psycopg3]==4.1.5'

COPY patroni.yml /etc/patroni.yml

# Patroni «PID 1» initializes and stops PostgreSQL itself.
USER postgres
STOPSIGNAL SIGTERM
ENTRYPOINT ["patroni"]
CMD ["/etc/patroni.yml"]
