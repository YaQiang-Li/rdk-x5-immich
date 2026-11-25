# immich machine-learning with the RDK X5 BPU session.
# Build on the board: the BPU runtime libraries are copied from its hobot-dnn.
ARG BASE=ghcr.io/immich-app/immich-machine-learning:v3.4.2
FROM ${BASE}

# the overlay replaces upstream files: refuse a base they were not written for
COPY upstream.sha256 /tmp/upstream.sha256
RUN cd /usr/src && sha256sum -c /tmp/upstream.sha256

COPY hobot-lib/ /opt/hobot/lib/
COPY overlay/immich_ml/ /usr/src/immich_ml/
ENV LD_LIBRARY_PATH=/opt/hobot/lib
