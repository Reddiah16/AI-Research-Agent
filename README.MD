# 🧠 AI Research Agent

> A production-style multi-agent AI system that autonomously researches topics, gathers information from the web, and generates structured reports using LangGraph and local LLMs.

## ✨ Features

- 🤖 Multi-Agent Architecture
- 🔍 Autonomous Web Research
- 📝 Structured Report Generation
- 📚 Research History Tracking
- 🗄 PostgreSQL Persistence
- 🌐 DuckDuckGo Integration
- 📊 Analytics Dashboard (KPIs, charts, execution history)
- 🔐 Environment-Based Secrets Configuration (no hardcoded credentials)
- ⚡ Fully Local Execution (No API Keys)
- 📥 Downloadable Reports

---

## 🏗 Architecture

```text
User Query
     │
     ▼
Planner Agent
     │
     ▼
Research Agent
     │
DuckDuckGo Search
     │
     ▼
Editor Agent
     │
     ▼
Structured Report
     │
     ▼
PostgreSQL + Streamlit
```

---

## ⚙ Tech Stack

| Category | Technologies |
|------------|-------------|
| Language | Python |
| Framework | LangGraph |
| LLM | Ollama + Qwen3 |
| Search | DuckDuckGo |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| UI | Streamlit |
| Charts | Altair / Vega-Lite |
| Validation | Pydantic |
| Configuration | python-dotenv (`DATABASE_URL`) |

---

## 📂 Project Structure

```text
RESEARCH_AGENT/
├── agents/
├── benchmarks/
├── config/
├── database/
├── generation/
├── graph/
├── schemas/
├── tools/
├── ui/
├── screenshots/
├── tests/
├── .env.example      # configuration template (tracked)
├── .env              # real credentials (git-ignored, never committed)
├── app.py
└── requirements.txt
```

---

## 📸 Preview

### Home Page

![](screenshots/home_page.png)

### Research Workflow

![](screenshots/research_progress.png)

### Final Report

![](screenshots/final_report.png)

---

## 📊 Analytics Dashboard

The **📊 Analytics** tab renders live research metrics straight from PostgreSQL —
KPI cards, four charts and a filterable execution-history table.

KPI overview:

![](screenshots/final_1_top.png)

Chart A — execution status distribution · Chart B — execution time per run:

![](screenshots/final_2_charts_ab.png)

Chart C — daily research activity · Chart D — unique sources per execution:

![](screenshots/final_3_charts_cd.png)

Execution history table (query, status, duration, sources, report, timestamp):

![](screenshots/final_4_history.png)

Full dashboard:

![](screenshots/analytics_final_dashboard.png)

---

## 📈 Performance Results (local testing observations)

> **Scope and disclaimer.** These numbers come from a **single developer
> machine** running the real end-to-end LangGraph pipeline locally (Ollama
> + DuckDuckGo search + PostgreSQL). They are **observations from local
> testing, not production guarantees** — throughput, latency and success
> rates depend on hardware, model choice, network access and search-engine
> behaviour, and are not benchmarked under concurrent or load conditions.

Snapshot of the PostgreSQL metrics table at the time of writing
(18 recorded research executions):

| Metric | Observed value | Notes |
|---|---|---|
| Total executions | **18** | All rows in `research_sessions` |
| Successful | **18** | `SUCCESS` / legacy `complete` |
| Failed | **0** | `FAILED` / `error` / `complete_unstructured` |
| Success rate | **100 %** | 18 / 18 |
| Average execution time | **127.8 s** | Mean of the **13** runs that recorded timing; 5 earlier rows pre-date performance tracking and are reported as *no timing data* (never backfilled or estimated) |
| Average unique sources per execution | **8.8** | 159 distinct source URLs across the 18 runs, computed with `COUNT(DISTINCT url)` |
| Reports generated | **18** | Rows in `research_reports` |

Individual run durations observed in this local environment ranged from
about **98 s to 223 s**, and the spread is visible in Chart B above. Every
value in this table is read from the database through the same service
functions the dashboard uses — nothing is hardcoded or fabricated.

---

## 🚀 Getting Started

### 1. Install

```bash
git clone <repository-url>

cd RESEARCH_AGENT

python -m venv venv

venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure the database (required)

The PostgreSQL connection string is **never hardcoded**. It is read from the
`DATABASE_URL` environment variable, which is loaded from a git-ignored
`.env` file:

```bash
cp .env.example .env
```

Then edit `.env` and set your own credentials:

```text
DATABASE_URL=postgresql://<db_user>:<db_password>@<host>:5432/<database_name>
```

`.env` is excluded by `.gitignore` (`.env`, `.env.*`, with only `.env.example`
tracked), so real credentials never reach the repository. Ollama must also be
running locally. If `DATABASE_URL` is missing, the app fails fast with setup
guidance rather than attempting a connection.

### 3. Run

```bash
streamlit run app.py
```

### 4. Verify

```bash
python tests/test_analytics_dashboard.py
python tests/test_metrics_persistence.py
python tests/test_performance_tracking.py
python tests/test_web_search.py
```

---

## 🎯 Skills Demonstrated

- Agentic AI
- Multi-Agent Systems
- LangGraph Workflows
- LLM Integration
- Prompt Engineering
- PostgreSQL Design
- Streamlit Development
- Software Architecture

---

## 🔮 Future Improvements

- PDF Export
- FastAPI Backend
- Docker Support
- Redis Cache
- Parallel Search
- Authentication

---

⭐ If you found this project interesting, consider giving it a star.
created By Reddaih :)