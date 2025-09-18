FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
# react-leaflet requires legacy peer resolution with React 19
RUN npm ci --legacy-peer-deps
COPY . .
ARG VITE_TEST_LOGIN_EMAIL
ARG VITE_TEST_LOGIN_PASSWORD
ARG VITE_AUTO_LOGIN
ENV VITE_TEST_LOGIN_EMAIL=${VITE_TEST_LOGIN_EMAIL}
ENV VITE_TEST_LOGIN_PASSWORD=${VITE_TEST_LOGIN_PASSWORD}
ENV VITE_AUTO_LOGIN=${VITE_AUTO_LOGIN}
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx","-g","daemon off;"]

