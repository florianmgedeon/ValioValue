# One container for the demo: built React UI + buyer backend (public) + mcp-server (internal).
# docker build -t valiovalue . && docker run -p 8000:8000 --env-file .env valiovalue

FROM node:22-slim AS frontend
WORKDIR /src
COPY contracts ./contracts
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend ./frontend
ARG VITE_SIGNER_NAME="Anna Virtanen"
ENV VITE_API_BASE_URL=/api VITE_MOCK=0 VITE_SIGNER_NAME=${VITE_SIGNER_NAME}
RUN cd frontend && npm run build

FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
COPY backend/requirements.txt ./backend/
COPY mcp-server/requirements.txt ./mcp-server/
RUN pip install -r requirements.txt
COPY . .
COPY --from=frontend /src/frontend/dist ./frontend/dist
RUN chmod +x scripts/start_container.sh
EXPOSE 8000
CMD ["scripts/start_container.sh"]
