const mongoose = require('mongoose');
const User = require('../models/User');
const ApiError = require('../utils/ApiError');
const asyncHandler = require('../utils/asyncHandler');

const STAFF = ['doctor', 'driver'];
/* accounts created before the status field existed have no status: they are active */
const byStatus = (s) => (s === 'active' ? { $or: [{ status: 'active' }, { status: { $exists: false } }] } : { status: s });

/** GET /api/admin/users?status=pending|active|rejected   (admin)   doctors and ambulance drivers only */
exports.listStaff = asyncHandler(async (req, res) => {
  const filter = { role: { $in: STAFF } };
  if (['pending', 'active', 'rejected'].includes(req.query.status)) Object.assign(filter, byStatus(req.query.status));
  if (STAFF.includes(req.query.role)) filter.role = req.query.role;
  const users = await User.find(filter).sort({ createdAt: -1 });
  const count = async (s) => User.countDocuments({ role: { $in: STAFF }, ...byStatus(s) });
  res.json({ success: true, count: users.length, data: { users, counts: { pending: await count('pending'), active: await count('active'), rejected: await count('rejected') } } });
});

/** PATCH /api/admin/users/:id/status   body { status: 'active' | 'rejected' }   approve, reject or revoke a doctor / driver */
exports.setStatus = asyncHandler(async (req, res) => {
  const status = req.body.status;
  if (!['active', 'rejected'].includes(status)) throw ApiError.badRequest('status must be active or rejected.', { status: 'Invalid status.' });
  const user = mongoose.isValidObjectId(req.params.id) ? await User.findById(req.params.id) : null;
  if (!user || !STAFF.includes(user.role)) throw ApiError.notFound('Doctor or driver account not found.');
  user.status = status;
  await user.save();
  res.json({ success: true, message: user.name + (status === 'active' ? ' approved.' : ' rejected.'), data: { user } });
});
