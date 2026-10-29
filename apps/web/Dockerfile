# The web interface for Azure: the static build behind nginx, no Node at run time (ADR-027).
# Build once, promote the same digest through environments: nothing deployment-specific is baked in.
# Sign-in settings and the origins the page may talk to are read from the environment when it starts.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
# The build also seals the content policy (a hash per inline script); render-edge turns it into the
# nginx header file, with the deployment's connection origins left as a variable.
RUN npm run build && node scripts/render-edge.mjs out edge

FROM nginxinc/nginx-unprivileged:1.30-alpine AS runtime
# The stable nginx line, and on top the fixes Alpine has published since the base image was built
# (openssl, curl, zlib, expat ...), so what runs is what a build today would give. Back to the image's own user after.
USER root
RUN apk upgrade --no-cache
USER 101
COPY --from=build /app/out /usr/share/nginx/html
COPY --from=build /app/edge/headers.inc.template /etc/nginx/templates/headers.inc.template
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
# An executable .envsh file is sourced (not run) by the nginx entrypoint, before the templates are
# rendered: what it exports reaches them.
COPY --chmod=0755 docker/15-nodo-runtime.envsh /docker-entrypoint.d/15-nodo-runtime.envsh

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
