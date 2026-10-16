const path = require('path');
require('dotenv').config();

module.exports = {
  env: process.env.NODE_ENV || 'development',
  port: Number(process.env.PORT) || 5000,
  mongoUri: process.env.MONGO_URI || 'mongodb://127.0.0.1:27017/medibridge',
  jwtSecret: process.env.JWT_SECRET || 'dev-only-secret-change-me',
  jwtExpires: process.env.JWT_EXPIRES_IN || '7d',
  /* only the origin (scheme + host + port) matters for CORS, so a trailing slash or a path like /api is removed instead of silently blocking the website */
  clientUrls: (process.env.CLIENT_URL || 'http://localhost:5173').split(',').map(s => s.trim()).filter(Boolean).map(s => { try { return new URL(s).origin; } catch (e) { return s; } }),
  uploadDir: path.resolve(process.env.UPLOAD_DIR || 'uploads'),
  stageStepMs: Number(process.env.STAGE_STEP_MS) || 6000
};
