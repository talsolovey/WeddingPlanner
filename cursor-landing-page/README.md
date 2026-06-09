# Vow — AI Wedding Planning Agent (Landing Page)

A simple landing page for **Vow**, an AI agent that helps engaged couples plan
their wedding — managing vendors, budgets, contracts, guest lists, deadlines,
and decisions in one place.

## Features

- **Hero + features section** describing what Vow does:
  - Reads vendor contracts to flag hidden fees and cancellation terms
  - Parses WhatsApp and email exports to extract tasks and decisions
  - Compares vendors side by side on budget and fit
  - Tracks payments and forecasts final cost
  - Manages RSVPs and guest details
  - Tells couples what needs attention next
- **Interactive demo button** that reveals a live "weekly brief" mockup of what
  Vow might show a couple that week.
- **Signup form** that collects email addresses and stores them to
  `signups.json`.

## Tech stack

- Node.js + Express (static hosting + signup API)
- Vanilla HTML, CSS, and JavaScript (no build step)

## Getting started

```bash
npm install
npm start
```

Then open <http://localhost:3000>.

For auto-reload during development:

```bash
npm run dev
```

## How signups are stored

When someone submits the form, the server validates the email and appends it to
`signups.json` in the project root:

```json
[
  { "email": "you@example.com", "signedUpAt": "2026-06-09T08:30:00.000Z" }
]
```

Duplicate emails are ignored. The file is created automatically on the first
signup.

## Project structure

```
.
├── server.js          # Express server + /api/signup endpoint
├── package.json
├── public/
│   ├── index.html     # Landing page
│   ├── styles.css     # Styling
│   └── app.js         # Demo toggle + signup form logic
└── signups.json       # Created at runtime (collected emails)
```
