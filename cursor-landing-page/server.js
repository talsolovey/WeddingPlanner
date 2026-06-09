import express from "express";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { promises as fs } from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;
const SIGNUPS_FILE = join(__dirname, "signups.json");

app.use(express.json());
app.use(express.static(join(__dirname, "public")));

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

async function readSignups() {
  try {
    const raw = await fs.readFile(SIGNUPS_FILE, "utf-8");
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
}

async function writeSignups(signups) {
  await fs.writeFile(SIGNUPS_FILE, JSON.stringify(signups, null, 2), "utf-8");
}

app.post("/api/signup", async (req, res) => {
  const email = (req.body?.email || "").trim().toLowerCase();

  if (!EMAIL_REGEX.test(email)) {
    return res.status(400).json({ error: "Please enter a valid email address." });
  }

  try {
    const signups = await readSignups();

    if (signups.some((s) => s.email === email)) {
      return res.status(200).json({ message: "You're already on the list!", alreadySignedUp: true });
    }

    signups.push({ email, signedUpAt: new Date().toISOString() });
    await writeSignups(signups);

    return res.status(201).json({ message: "You're on the list! We'll be in touch soon." });
  } catch (err) {
    console.error("Failed to save signup:", err);
    return res.status(500).json({ error: "Something went wrong. Please try again." });
  }
});

app.listen(PORT, () => {
  console.log(`Vow landing page running at http://localhost:${PORT}`);
});
