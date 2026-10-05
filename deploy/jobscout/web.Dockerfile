FROM nginx:stable-alpine
COPY docker/nginx/jobscout.conf /etc/nginx/nginx.conf
COPY jobscout-web/index.html jobscout-web/style.css jobscout-web/runtime-config.js jobscout-web/app.js jobscout-web/core.js jobscout-web/api-client.js jobscout-web/tracker-state.js /usr/share/nginx/jobscout/
COPY LICENSE /usr/share/nginx/jobscout/LICENSE
