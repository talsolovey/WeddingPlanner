const express = require('express');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;
const DATA_FILE = path.join(__dirname, 'emails.json');

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// --- helpers -------------------------------------------------------------
function readList() {
  try {
    const raw = fs.readFileSync(DATA_FILE, 'utf8').trim();
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    // File missing or unreadable: start clean rather than crash.
    return [];
  }
}

function writeList(list) {
  fs.writeFileSync(DATA_FILE, JSON.stringify(list, null, 2) + '\n', 'utf8');
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// --- routes --------------------------------------------------------------
app.post('/api/signup', (req, res) => {
  const email = (req.body && typeof req.body.email === 'string' ? req.body.email : '').trim();

  if (!email) {
    return res.status(400).json({ error: 'Enter an email so we know where to reach you.' });
  }
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return res.status(400).json({ error: 'That email doesn’t look right — mind checking it?' });
  }

  const list = readList();
  const exists = list.some((e) => e.email.toLowerCase() === email.toLowerCase());

  if (exists) {
    return res.json({ ok: true, already: true, count: list.length });
  }

  list.push({ email, joinedAt: new Date().toISOString() });

  try {
    writeList(list);
  } catch (err) {
    return res.status(500).json({ error: 'Something went wrong saving your spot. Try again in a moment.' });
  }

  return res.json({ ok: true, already: false, count: list.length });
});

app.get('/api/count', (req, res) => {
  res.json({ count: readList().length });
});

app.listen(PORT, () => {
  console.log(`Vow is running at http://localhost:${PORT}`);
});
