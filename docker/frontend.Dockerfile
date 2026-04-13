FROM node:20-alpine AS build

WORKDIR /app/frontend

RUN corepack enable

COPY frontend/package.json ./package.json
COPY patches /app/patches
RUN pnpm install --no-frozen-lockfile

COPY frontend /app/frontend

ARG VITE_FEISHU_APP_ID=""
ARG VITE_FEISHU_SCOPE_LIST=""
ARG VITE_BASE_URL="/"
ENV VITE_FEISHU_APP_ID=${VITE_FEISHU_APP_ID}
ENV VITE_FEISHU_SCOPE_LIST=${VITE_FEISHU_SCOPE_LIST}
ENV VITE_BASE_URL=${VITE_BASE_URL}

RUN pnpm build

FROM nginx:1.27-alpine

COPY docker/frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/public /usr/share/nginx/html

EXPOSE 80
