FROM golang:1.26-alpine3.23 AS builder

ARG XRAY_VER='v26.6.1'

RUN apk add --no-cache bash git curl

WORKDIR /go/src/XTLS/Xray-core
RUN git clone https://github.com/XTLS/Xray-core.git . && \
    git checkout ${XRAY_VER} && \
    CGO_ENABLED=0 go build -o xray -trimpath -ldflags "-s -w -buildid=" ./main

RUN curl -fsSLo geoip.dat --retry 3 --retry-all-errors --connect-timeout 10 --max-time 180 \
    https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat || touch geoip.dat
RUN curl -fsSLo geosite.dat --retry 3 --retry-all-errors --connect-timeout 10 --max-time 180 \
    https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat || touch geosite.dat


FROM alpine:3.23

COPY --from=builder /go/src/XTLS/Xray-core/xray         /usr/local/bin/
COPY --from=builder /go/src/XTLS/Xray-core/geosite.dat  /usr/local/bin/
COPY --from=builder /go/src/XTLS/Xray-core/geoip.dat    /usr/local/bin/

RUN apk --no-cache add bash python3 openssl curl jq moreutils tzdata ca-certificates

COPY run.sh         /run.sh
COPY dns-split-proxy.py /dns-split-proxy.py
COPY subscription-supervisor.py /subscription-supervisor.py
COPY proxy_xray     /proxy_xray

RUN chmod 755 /run.sh
RUN chmod 755 /dns-split-proxy.py
RUN chmod 755 /subscription-supervisor.py

ENTRYPOINT ["/run.sh"]
