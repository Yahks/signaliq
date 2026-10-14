# Next.js 14 production image.
#
# Three stages: deps (prod node_modules), builder (full + build),
# runner (copies the whole built tree). Simple and correct.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev

FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

# Copy the full built tree. Includes .next, node_modules (with
# dev deps — harmless at runtime), package.json, next.config.*,
# and public/ if it exists.
COPY --from=builder /app ./

# Drop dev dependencies to shrink the final image.
RUN npm prune --omit=dev

EXPOSE 3000
CMD ["npm", "run", "start"]
