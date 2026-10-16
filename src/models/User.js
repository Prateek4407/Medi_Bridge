const mongoose = require('mongoose');
const bcrypt = require('bcryptjs');
const plugin = require('./plugin');

const userSchema = new mongoose.Schema({
  role: { type: String, enum: ['patient', 'doctor', 'driver', 'admin'], required: true },
  name: { type: String, required: true, trim: true },
  email: { type: String, required: true, unique: true, lowercase: true, trim: true },
  phone: { type: String, required: true, unique: true, trim: true },
  password: { type: String, required: true, select: false },
  spec: { type: String, trim: true, default: '' },
  /* doctors and drivers register as 'pending' and cannot log in until an admin approves them. Accounts saved before this field existed count as active. */
  status: { type: String, enum: ['active', 'pending', 'rejected'], default: 'active' }
}, { timestamps: true });

userSchema.pre('save', async function () {
  if (this.isModified('password')) this.password = await bcrypt.hash(this.password, 10);
});
userSchema.methods.comparePassword = function (plain) { return bcrypt.compare(plain, this.password); };
userSchema.plugin(plugin);

module.exports = mongoose.model('User', userSchema);
