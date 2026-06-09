# WeddingOS — Three Ways to Build the Same Landing Page

The same landing page for _Vow_ (an AI wedding-planning agent), built three
different ways. The point is to show the difference in LLM autonomy: from a model
with no tools, to a custom harness, to a full agent.

## Layer 1 — Raw LLM (no tools)

The model only generates code — the files and server were assembled by hand.

```bash
cd web-landing-page
npm install
npm start
```

Open <http://localhost:3000>.

## Layer 2 — Custom harness (LLM + tools)

The model runs inside a custom harness, with tools to create files and check its
own work.

```bash
cd harness-landing-page
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python server.py
```

Open <http://localhost:5001>.

## Layer 3 — Agent harness (Cursor / Claude Code)

One instruction. The agent plans, writes the code, runs the server, and delivers
a working page.

```bash
cd cursor-landing-page
npm install
npm start
```

Open <http://localhost:3000>.

> Layers 1 and 3 both use port 3000, so run one at a time.
