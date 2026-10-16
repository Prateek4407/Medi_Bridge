/* Doctor / driver sign-up with administrator approval. Real Express app + real MongoDB (MONGO_URI_TEST). */
const os = require('os'), path = require('path'), fs = require('fs');
process.env.NODE_ENV = 'test';
process.env.JWT_SECRET = 'test-secret';
process.env.UPLOAD_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'mb-uploads-'));
process.env.ADMIN_EMAIL = 'boss@t.test';
process.env.ADMIN_PASSWORD = 'bosspass1';
process.env.ADMIN_PHONE = '9000000077';

const mongoose = require('mongoose');
const request = require('supertest');
const app = require('../src/app');
const connectDB = require('../src/config/db');
const { ensureAdmin, seedHospitals } = require('../src/utils/seed');
const User = require('../src/models/User');

const api = () => request(app);
let n = 0;
const phone = () => '9' + String(300000000 + ++n);
const auth = (t) => ({ Authorization: 'Bearer ' + t });
const signup = (role, o = {}) => api().post('/api/auth/register').send({ role, name: 'Staff Person', email: `s${++n}@t.test`, phone: phone(), password: 'secret1', ...(role === 'doctor' ? { spec: 'Cardiology' } : {}), ...o });
const login = (email, role, password = 'secret1') => api().post('/api/auth/login').send({ identifier: email, password, role });

let admin, patient;

beforeAll(async () => {
  await connectDB(process.env.MONGO_URI_TEST || 'mongodb://127.0.0.1:27017/medibridge_test');
  await mongoose.connection.dropDatabase();
  await Promise.all(Object.values(mongoose.models).map(m => m.init()));
  await seedHospitals();
  await ensureAdmin();
  admin = (await login(process.env.ADMIN_EMAIL, 'admin', 'bosspass1')).body.data;
  const p = await api().post('/api/auth/register').send({ role: 'patient', name: 'Pat Ient', email: 'pat@t.test', phone: phone(), password: 'secret1' });
  patient = p.body.data;
});
afterAll(async () => {
  await mongoose.connection.dropDatabase();
  await mongoose.disconnect();
  fs.rmSync(process.env.UPLOAD_DIR, { recursive: true, force: true });
});

describe('Administrator account', () => {
  test('created from ADMIN_EMAIL / ADMIN_PASSWORD, and cannot be self-registered', async () => {
    expect(admin.user.role).toBe('admin');
    expect((await signup('admin')).status).toBe(400);
  });
  test('ensureAdmin never overwrites an existing account', async () => {
    const before = await User.countDocuments({ role: 'admin' });
    await ensureAdmin();
    expect(await User.countDocuments({ role: 'admin' })).toBe(before);
  });
});

describe('Doctor sign-up needs approval', () => {
  let doc;
  test('patients still get a session immediately', () => { expect(patient.token).toBeTruthy(); expect(patient.user.status).toBe('active'); });
  test('a doctor can register but gets no session', async () => {
    const r = await signup('doctor'); doc = r.body.data.user;
    expect(r.status).toBe(201); expect(r.body.pending).toBe(true); expect(r.body.data.token).toBeUndefined(); expect(doc.status).toBe('pending');
  });
  test('doctor needs a speciality', async () => {
    const r = await signup('doctor', { spec: '' }); expect(r.status).toBe(400); expect(r.body.errors.spec).toBeTruthy();
  });
  test('a pending doctor cannot log in', async () => {
    const r = await login(doc.email, 'doctor'); expect(r.status).toBe(403); expect(r.body.message).toMatch(/waiting for administrator approval/);
  });
  test('only admins can see or change approvals', async () => {
    expect((await api().get('/api/admin/users')).status).toBe(401);
    expect((await api().get('/api/admin/users').set(auth(patient.token))).status).toBe(403);
    expect((await api().patch(`/api/admin/users/${doc.id}/status`).set(auth(patient.token)).send({ status: 'active' })).status).toBe(403);
  });
  test('admin sees the pending request with counts', async () => {
    const r = await api().get('/api/admin/users?status=pending').set(auth(admin.token));
    expect(r.status).toBe(200); expect(r.body.data.users.some(u => u.id === doc.id)).toBe(true);
    expect(r.body.data.counts.pending).toBeGreaterThanOrEqual(1);
    expect(r.body.data.users.every(u => u.password === undefined)).toBe(true);
  });
  test('approving lets the doctor log in and use doctor features', async () => {
    const a = await api().patch(`/api/admin/users/${doc.id}/status`).set(auth(admin.token)).send({ status: 'active' });
    expect(a.status).toBe(200); expect(a.body.data.user.status).toBe('active');
    const l = await login(doc.email, 'doctor'); expect(l.status).toBe(200);
    expect((await api().get('/api/documents').set(auth(l.body.data.token))).status).toBe(200);
  });
  test('revoking blocks the doctor even with an old token', async () => {
    const l = await login(doc.email, 'doctor'); const token = l.body.data.token;
    await api().patch(`/api/admin/users/${doc.id}/status`).set(auth(admin.token)).send({ status: 'rejected' });
    expect((await login(doc.email, 'doctor')).status).toBe(403);
    expect((await api().get('/api/documents').set(auth(token))).status).toBe(403);
  });
  test('validation: unknown status, unknown id, patients cannot be targeted', async () => {
    expect((await api().patch(`/api/admin/users/${doc.id}/status`).set(auth(admin.token)).send({ status: 'banana' })).status).toBe(400);
    expect((await api().patch('/api/admin/users/not-an-id/status').set(auth(admin.token)).send({ status: 'active' })).status).toBe(404);
    expect((await api().patch(`/api/admin/users/${patient.user.id}/status`).set(auth(admin.token)).send({ status: 'rejected' })).status).toBe(404);
  });
});

describe('Driver sign-up needs approval', () => {
  test('driver is pending, cannot be linked to an ambulance until approved', async () => {
    const r = await signup('driver'); const drv = r.body.data.user;
    expect(r.body.pending).toBe(true);
    const doctor = await signup('doctor'); await User.updateOne({ email: doctor.body.data.user.email }, { status: 'active' });
    const dl = (await login(doctor.body.data.user.email, 'doctor')).body.data.token;
    const body = { registration: 'DL1CAP0001', hospital: 'H1', driverEmail: drv.email };
    const blocked = await api().post('/api/ambulances').set(auth(dl)).send(body);
    expect(blocked.status).toBe(400); expect(blocked.body.errors.driverEmail).toBeTruthy();
    await api().patch(`/api/admin/users/${drv.id}/status`).set(auth(admin.token)).send({ status: 'active' });
    expect((await api().post('/api/ambulances').set(auth(dl)).send(body)).status).toBe(201);
  });
});

describe('Existing accounts without a status field keep working', () => {
  test('a legacy doctor (no status) can log in and is counted as active', async () => {
    const s = await signup('doctor'); const email = s.body.data.user.email;
    await User.collection.updateOne({ email }, { $unset: { status: '' } });
    expect((await login(email, 'doctor')).status).toBe(200);
    const list = await api().get('/api/admin/users?status=active').set(auth(admin.token));
    expect(list.body.data.users.some(u => u.email === email)).toBe(true);
  });
});
