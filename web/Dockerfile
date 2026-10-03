# Base images via Google's Docker Hub mirror, as for the API image.
ARG NODE_IMAGE=mirror.gcr.io/library/node:22-alpine
ARG NGINX_IMAGE=mirror.gcr.io/library/nginx:1.27-alpine

FROM ${NODE_IMAGE} AS builder

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY i18n ./i18n
COPY src ./src

# Baked in at build time; the default same-origin path keeps the image portable.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build


FROM ${NGINX_IMAGE} AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget --spider -q http://127.0.0.1/ || exit 1
