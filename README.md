# Fantasy Streams

A lightweight, powerful web application designed to give Head-to-Head (H2H) fantasy hockey managers a competitive edge. This app connects directly to Yahoo! Fantasy Sports to provide advanced league analytics, lineup optimization, and automated player transactions.

## Tech Stack

* **Backend:** Python (Flask), Gevent, Gunicorn
* **Database:** PostgreSQL (Primary Data), Redis (Job Queues & Caching)
* **Background Workers:** RQ (Redis Queue)
* **Frontend:** HTML5, Tailwind CSS, JavaScript (Vanilla ES6)
* **APIs:** Yahoo! Fantasy Sports API
* **Deployment:** Render.com

## Core Features

* **League Analytics:** Instantly fetch and cache league history and matchups.
* **Manager Tools:** Advanced free agent filtering, goalie start planning, and trade analysis.
* **Automation:** Schedule waiver wire add/drops to execute automatically.
