FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
# react-leaflet requires legacy peer resolution with React 19
RUN npm ci --legacy-peer-deps
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx","-g","daemon off;"]

