FROM node:22-bookworm-slim AS web
WORKDIR /app
COPY package.json package-lock.json ./
COPY packages ./packages
COPY apps/web ./apps/web
COPY apps/mobile/package.json ./apps/mobile/package.json
RUN npm ci
ARG REMOTE_ORIGIN
ENV NEXT_PUBLIC_REMOTE_SERVICE_URL=$REMOTE_ORIGIN
ENV NEXT_TELEMETRY_DISABLED=1
RUN test -n "$REMOTE_ORIGIN" && npm run build -w apps/web
FROM nginx:1.28-alpine
COPY --from=web /app/apps/web/out /usr/share/nginx/html
COPY infra/shared-remote/edge.conf.template /etc/nginx/templates/default.conf.template
ENV NGINX_ENVSUBST_FILTER=REMOTE_DOMAIN
