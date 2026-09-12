# Multi-stage build: compile the client with Node, ship only the static output
# behind nginx. The Node toolchain and node_modules never reach the final image.
#
# Both base images come through Google's pull-through cache of Docker Hub for
# the same reason as the API image; override to go direct.
ARG NODE_IMAGE=mirror.gcr.io/library/node:22-alpine
ARG NGINX_IMAGE=mirror.gcr.io/library/nginx:1.27-alpine

FROM ${NODE_IMAGE} AS builder

WORKDIR /build

# Manifests first: editing source must not invalidate the install layer.
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src

# Vite inlines VITE_* at build time, so this is baked into the bundle and the
# image is specific to it. The default is a same-origin path served by the nginx
# stage below, which is why the built image is not tied to a host or port.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build


FROM ${NGINX_IMAGE} AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

# busybox wget; the image has no curl.
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget --spider -q http://127.0.0.1/ || exit 1
