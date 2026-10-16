const env = require('./src/config/env');
const connectDB = require('./src/config/db');
const app = require('./src/app');
const { seedHospitals, seedAmbulances, ensureAdmin } = require('./src/utils/seed');


(async () => {
  if (env.env === 'production' && !process.env.JWT_SECRET) throw new Error('JWT_SECRET is not set. Add it in the Render environment settings.');
  await connectDB(env.mongoUri);
  await seedHospitals();
  await seedAmbulances();
  await ensureAdmin();
  app.listen(env.port, () => console.log(`MediBridge API running on http://localhost:${env.port}/api`));
})().catch(e => { console.error('Failed to start:', e.message); process.exit(1); });
