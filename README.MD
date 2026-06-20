# 🧠 AI Research Agent

> A production-style multi-agent AI system that autonomously researches topics, gathers information from the web, and generates structured reports using LangGraph and local LLMs.

## ✨ Features

- 🤖 Multi-Agent Architecture
- 🔍 Autonomous Web Research
- 📝 Structured Report Generation
- 📚 Research History Tracking
- 🗄 PostgreSQL Persistence
- 🌐 DuckDuckGo Integration
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
| Validation | Pydantic |

---

## 📂 Project Structure

```text
RESEARCH_AGENT/
├── agents/
├── config/
├── database/
├── generation/
├── graph/
├── schemas/
├── tools/
├── ui/
├── screenshots/
├── tests/
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

## 🚀 Getting Started

```bash
git clone <repository-url>

cd RESEARCH_AGENT

python -m venv venv

venv\Scripts\activate

pip install -r requirements.txt

streamlit run app.py
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