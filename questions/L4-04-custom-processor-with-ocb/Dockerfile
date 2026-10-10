FROM golang:1.26.0@sha256:fb612b7831d53a89cbc0aaa7855b69ad7b0caf603715860cf538df854d047b84 AS build

ENV CGO_ENABLED=0
WORKDIR /src

RUN go install go.opentelemetry.io/collector/cmd/builder@v0.162.0

COPY builder-config.yaml ./
COPY processor ./processor

RUN builder --config=/src/builder-config.yaml \
    && cp /src/_build/otelcol-custom /otelcol-custom

FROM scratch

COPY --from=build /otelcol-custom /otelcol-custom

USER 65532:65532
EXPOSE 4317 4318 8888 13133
ENTRYPOINT ["/otelcol-custom"]
CMD ["--config=/etc/otelcol-contrib/config.yaml"]
