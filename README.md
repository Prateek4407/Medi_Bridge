# MediBridge Backend (Express + MongoDB, MVC)

REST API for the MediBridge frontend: auth (patient/doctor/driver), hospital finder, emergency cases with live tracking,
ambulance fleet management with automatic dispatch and driver GPS, doctor appointment slots, demo payments, and patient
documents / medical history with consent control and an access (audit) log.

Full endpoint list with request/response examples: **[docs/API.md](docs/API.md)**. The endpoints added for ambulances, consent and audit are listed in section 'What is new' below.

## Structure (MVC)
```
server.js                 starts the server (connect DB, seed hospitals, listen)
src/
  app.js                  Express app: security, CORS, JSON, routes, error handling
  config/                 env.js (settings), db.js (MongoDB connection)
  models/      (M)        User, Hospital, Case, Slot, Invoice, Document, Ambulance, Consent, AuditLog  (Mongoose schemas)
  controllers/ (C)        auth, hospital, case, slot, invoice, document, ambulance, trust (consent + audit)
  routes/                 one router per resource, mounted under /api
  middleware/             auth (JWT + roles), upload (multer), errorHandler
  services/               caseService (stages/ETA/timeline), ambulanceService (dispatch, positions), fileStorage (GridFS),
                          consentService, auditService, realtime (live tracking pushes), invoiceService
  utils/                  ApiError, asyncHandler, validators, seed
seeds/                    hospital data + `npm run seed`
tests/api.test.js         integration tests (core API)
tests/fleet.test.js       integration tests (ambulances, dispatch, GPS, consent, audit)
docs/API.md               API documentation
uploads/                  only used to read documents uploaded BEFORE the move to MongoDB (legacy)
```
In an API, the "View" of MVC is the JSON each controller returns (`present()` functions in the case/slot controllers
and the `toJSON` rules in `models/plugin.js`).

## Run
```
npm install
node -e "require('fs').closeSync(require('fs').openSync('.env', 'a'))"  # create .env on Windows; use `touch .env` on macOS/Linux
npm run seed                # optional: demo accounts patient@demo.test / doctor@demo.test (password demo1234)
npm run dev                 # http://localhost:5000/api/health
```
The server loads `.env` with `dotenv`; there is no checked-in `.env.example`, so create the file yourself and never commit it.
Hospitals and the demo ambulance fleet are seeded automatically on first start (the demo driver login is
`driver@demo.test` after `npm run seed`).

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORT` | `5000` | HTTP port |
| `MONGO_URI` | `mongodb://127.0.0.1:27017/medibridge` | MongoDB database used by the server |
| `MONGO_URI_TEST` | `mongodb://127.0.0.1:27017/medibridge_test` | Database used by Jest; it is dropped on every run |
| `JWT_SECRET` | `dev-only-secret-change-me` | JWT signing key; required in production |
| `JWT_EXPIRES_IN` | `7d` | JWT lifetime |
| `CLIENT_URL` | `http://localhost:5173` | Comma-separated frontend origins allowed by CORS |
| `UPLOAD_DIR` | `uploads` | Legacy on-disk fallback for files uploaded before GridFS |
| `STAGE_STEP_MS` | `6000` | Timer interval for demo-case stage progression |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | unset | Optional first administrator created at startup |
| `ADMIN_NAME` / `ADMIN_PHONE` | `Administrator` / `9000000001` | Optional administrator profile fields |

For example, a local `.env` can contain:
```
MONGO_URI=mongodb://127.0.0.1:27017/medibridge
JWT_SECRET=replace-this-with-a-long-random-value
CLIENT_URL=http://localhost:5173
```

## Test
```
npm test
```
Uses `MONGO_URI_TEST` (default `mongodb://127.0.0.1:27017/medibridge_test`). **That database is dropped on every run**, so never point it at real data.
Covers auth and approval, validation, role rules, ambulance availability and assignment, GPS proximity progression,
driver pickup confirmation, SSE/polling-compatible tracking, slot overlap / double booking, payments, and document
upload / permissions / file download. A local MongoDB instance must be running before `npm test`.

## What is new

### Doctor and driver sign-up with administrator approval
* Anyone can register as a patient and gets a session straight away.
* **Doctors and ambulance drivers can also register on the website, but their account starts as `pending`.** They get no token
  and cannot log in (or use an old token) until an **administrator** approves them. Rejected or revoked accounts are blocked everywhere.
* The first administrator is created on server start from environment variables (nothing is created if they are not set):
  `ADMIN_EMAIL`, `ADMIN_PASSWORD` (8+ characters), optional `ADMIN_NAME`, `ADMIN_PHONE`. An existing account is never changed.
  Admins cannot be created through the public register form.
* Admin endpoints: `GET /api/admin/users?status=pending|active|rejected` and `PATCH /api/admin/users/:id/status` with `{ "status": "active" | "rejected" }`.
* Accounts created before this feature have no status and count as active, so nobody is locked out.
* An ambulance can only be linked to a driver whose account has been approved.
* Doctors must give a speciality when registering.
* `npm run seed` still creates the demo patient, doctor and driver (active). It does not create an admin, so the demo password never
  becomes an admin login on a real database.

### Medical documents survive redeploys
Files are now stored **inside MongoDB (GridFS)**, not on the server disk, so a Render redeploy no longer erases them.
Nothing extra to set up: it uses the same `MONGO_URI`. Documents uploaded before this change still open if their file is
still on disk; otherwise the patient sees "please upload it again". Files are limited to 10 MB each (same as before).
Atlas free tier has 512 MB total, so this suits a demo/academic project; for production use S3 or Cloudinary by changing
only `services/fileStorage.js`.

### Ambulance fleet and dispatch
* Doctors manage ambulances (vehicle number, base hospital, type, equipment, driver, status).
* A real emergency is automatically moved from request received to hospital confirmed, then the **nearest free ambulance**
  is claimed atomically, preferring the chosen hospital's own vehicles. It is freed when the case reaches the hospital
  or is restarted, and automatically after 30 minutes if a case is abandoned.
* If every ambulance is busy the case waits at hospital confirmation until a later tracking request finds a free vehicle;
  it does not pretend that an ambulance has been assigned.
* The Hospital Finder and the delay-prediction form show how many ambulances are free (`ambulances: { total, available }`).
* If no ambulance exists, cases use the old fully simulated flow (nothing breaks). Demo cases always retain timer-driven
  progression and the manual `/advance` and `/restart` controls.

### Real-time tracking (honest about what is real)
* `GET /api/cases/:caseId/stream` is a public SSE endpoint. It sends `data: {"case": ...}` immediately and every 1.5 seconds,
  sends an update immediately after a GPS change, emits `event: done` when the case completes, and closes after 10 minutes.
  Use `EventSource` where available and poll `GET /api/cases/:caseId/track` every 1–2 seconds as the fallback.
* A driver registers with role **driver**, an approved account is linked by a doctor, and the driver's phone sends
  `{ "lat": 28.61, "lng": 77.20 }` to `PATCH /api/ambulances/mine/location` (the Driver page sends it every 4 seconds).
* For a real case, fresh GPS within 100 m of the pickup point moves the case to **Pickup done**. The driver then calls
  `POST /api/ambulances/mine/cases/:caseId/pickup` to confirm the patient is loaded; fresh GPS within 100 m of the receiving
  hospital completes the case. Real cases do not advance from `STAGE_STEP_MS`.
* While GPS is fresh (<90 seconds), the position is labelled `source: "live"`. After completion, the final fresh fix is retained
  as `source: "last-known"`; when no usable GPS is available, the position is simulated and labelled `source: "simulated"`.
* Pushes use in-process pub/sub and therefore work on one server instance. For multiple instances, replace
  `services/realtime.js` with a Redis pub/sub implementation.

### Consent and audit history
* Patients can switch sharing of their documents with doctors on/off (`/api/consent`). Withdrawing hides every document
  from doctors (list, details, file) and the attempt is logged. Every decision is kept as history.
* Sending an emergency request while logged in records an `emergency-case` consent with the case id.
* Every upload, view, download, delete, blocked attempt and consent change is written to an append-only audit log.
  Patients read it at `GET /api/audit` (who opened what, when); doctors see only their own activity.
* No decision on file counts as "allowed" (the upload screen already says doctors can see files); the first upload
  writes an explicit grant.

| Method | URL | Who | Purpose |
|--------|-----|-----|---------|
| GET | `/api/ambulances/availability` | Public | Free / total ambulances, per hospital |
| GET | `/api/ambulances` | Doctor | Fleet list (filters `hospital`, `status`) |
| POST | `/api/ambulances` | Doctor | Add an ambulance (`registration`, `hospital`, `type`, `driverEmail`, ...) |
| PATCH | `/api/ambulances/:id` | Doctor | Edit / set status `available`, `offline`, `maintenance` |
| DELETE | `/api/ambulances/:id` | Doctor | Remove (not while on a case) |
| PATCH | `/api/ambulances/:id/location` | Doctor or linked driver | Set position `{ lat, lng }` |
| GET | `/api/ambulances/mine` | Driver | My ambulance |
| PATCH | `/api/ambulances/mine/location` | Driver | Send live GPS `{ lat, lng }` |
| PATCH | `/api/ambulances/mine/status` | Driver | `available` or `offline` |
| POST | `/api/ambulances/mine/cases/:caseId/pickup` | Driver | Confirm patient pickup after reaching the pickup point |
| GET | `/api/cases/:caseId/stream` | Public | Live tracking stream (SSE) |
| GET | `/api/consent` | Patient | Current consent + history |
| POST | `/api/consent` | Patient | `{ granted: true | false }` |
| GET | `/api/audit?limit=&action=` | Patient / Doctor | Access log (patient: my records, doctor: my activity) |

Case JSON now also contains `ambulance` (`registration`, `type`, `equipment`, `position`; the full view adds the driver's
name and phone) and `waitingForAmbulance`. Hospital JSON contains `ambulances`.

### Tracking and driver API examples

```bash
# Public polling view (safe to share)
curl http://localhost:5000/api/cases/CB-2041/track

# Public SSE stream; keep the connection open
curl -N http://localhost:5000/api/cases/CB-2041/stream

# Driver GPS (Bearer token from driver login)
curl -X PATCH http://localhost:5000/api/ambulances/mine/location \
  -H "Authorization: Bearer <driver-token>" -H "Content-Type: application/json" \
  -d "{\"lat\":28.5985,\"lng\":77.3430}"

# Driver confirms pickup after GPS reaches the pickup radius
curl -X POST http://localhost:5000/api/ambulances/mine/cases/CB-2041/pickup \
  -H "Authorization: Bearer <driver-token>"
```

The complete route and request/response reference is in [docs/API.md](docs/API.md). The public tracking view intentionally
omits phone, symptoms, landmark, relationship, and patient GPS; use the authenticated case endpoint for the full view.

## Deploy (e.g. Render + MongoDB Atlas)
Set `MONGO_URI`, `JWT_SECRET`, `CLIENT_URL`, `NODE_ENV=production`. Documents are stored in MongoDB, so they survive redeploys.
`UPLOAD_DIR` is no longer needed. Free Render instances sleep when idle: the first request after a pause is slow and open live
tracking streams are dropped (the page reconnects or falls back to polling).
