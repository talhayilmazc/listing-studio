FROM node:20-alpine

WORKDIR /app

# Install dependencies first for better layer caching. The named volume in
# docker-compose is seeded from this image's node_modules on first run.
COPY frontend/package.json ./package.json
RUN npm install

COPY frontend/ ./

EXPOSE 3000

CMD ["npm", "run", "dev"]
