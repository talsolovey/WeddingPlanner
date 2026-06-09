# Vow — landing page

A landing page for **Vow**, an AI wedding planning agent. It has:

- a **features list** of what the agent does,
- an **interactive button** that reveals a live "this week's brief" demo, and
- a **signup form** that stores email addresses to a JSON file (`emails.json`).

## Run it

You need [Node.js](https://nodejs.org) (v18+).

```bash
npm install      # installs express
npm start        # starts the server
```

Then open **http://localhost:3000**.

## Where signups go

Every email submitted through the form is appended to **`emails.json`** in this
folder, e.g.:

```json
[
  { "email": "you@email.com", "joinedAt": "2026-06-09T08:28:34.998Z" }
]
```

Duplicates are ignored (case-insensitive) and invalid addresses are rejected.
The footer shows a live count pulled from this file.

## Files

| File | What it is |
| --- | --- |
| `public/index.html` | The whole page — HTML, CSS, and JS in one file. |
| `server.js` | Express server: serves the page, handles `/api/signup` and `/api/count`. |
| `emails.json` | The stored signups (starts empty as `[]`). |
| `package.json` | Dependencies and the `start` script. |

## Notes

- Opening `public/index.html` directly (without the server) still renders the
  page and the demo. The form just can't save — it needs `npm start` for that.
- To change the demo content, edit the `.brief__items` section in
  `public/index.html`. To reset signups, set `emails.json` back to `[]`.
