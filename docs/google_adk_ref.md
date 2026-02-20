<https://www.youtube.com/watch?v=2BA_nF-bpws>

## 1) What this video is about (1:47–2:16)

Goal: Walk through **Google ADK**:

* Key features
* Unique value vs other open-source agent frameworks
* Demos:

  * simple weather tool agent
  * **bidirectional streaming** agent (voice/camera/screen)
  * data science multi-agent sample (BigQuery + code + RAG)

---

## 2) Quick start (2:23–2:48)

**Install:**

* `pip install google-adk`

Key point:

* Not limited to Google models. You can use:

  * **Gemini** (well integrated)
  * your own models via Vertex AI Model Garden (e.g., Llama)
  * other providers (via adapter usage later in tutorial)

---

## 3) Core capabilities of Google ADK (2:53–7:54)

### 3.1 Flexible orchestration (2:53–3:36)

Supports multiple orchestration patterns:

* **Sequential** (Agent 1 → 2 → 3)
* **Parallel** (run multiple agents concurrently)
* **Loop/Iterative** (agents iterate until a defined “optimal” condition)
* Custom logic possible (not limited to the 3 patterns)

### 3.2 Multi-agent architecture (3:36–3:57)

* Multiple specialized agents
* Routing can be:

  * by explicit orchestrator workflow
  * by intelligent agent decision (“which agent should handle this?”)

### 3.3 Rich tool ecosystem (3:57–4:46)

Tools can include:

* Python functions as tools
* Built-in tools (e.g., **Google Search** when using Gemini)
* Built-in code execution/interpreter tool
* Adapters to reuse tools from other frameworks (e.g., LangChain tools)
* **MCP support** (detailed later)

### 3.4 Deployment ready / production-stage (4:46–5:05)

* Package agents and deploy:

  * Vertex AI Agent Engine (wrapper)
  * Cloud Run / containers
  * anywhere you can run containers
  * even on-prem

### 3.5 Built-in evaluation (5:05–5:11)

* Evaluate both:

  * final response quality vs expected baseline
  * trace/tool usage correctness (what tools were called, in what order)

### 3.6 Responsible agents / human-in-the-loop (5:11–5:18)

* Uses **callbacks** for supervision (before/after executing sensitive actions)

### 3.7 ADK “unique values” called out (5:25–7:54)

* Strong integration with **GCP ecosystem**
* Built-in local **UI** for:

  * testing, tracing, evaluating
* **Bidirectional streaming**:

  * voice interaction (Gemini Live-style)
  * can use camera/screen so agent “sees” you
* “Google/DeepMind thought leadership” influences design
* Uses patterns Google uses internally for agents

---

## 4) How agents work in ADK (8:08–9:28)

Two ways to control “which agent/tool runs”:

1. **LLM-based decision**

   * Base agent decides to call a tool or another agent
2. **Workflow-based orchestration**

   * sequential / parallel / loop patterns

Tools can be:

* Python functions
* tools
* agents as tools
* MCP tools

---

## 5) Callbacks (Responsible guardrails) (9:34–10:43)

Callbacks can run:

* **Before** calling an agent
* **After** calling an agent
* **Before** calling a tool
* **After** calling a tool
* **Before/after model calls** (implied by “model callbacks” mentioned later)

Use case examples:

* Check for sensitive keywords
* Human approval before API calls
* Validate tool inputs/outputs

---

## 6) MCP integration patterns (10:43–11:51)

MCP = tool protocol (noted as initiated by Anthropic)

ADK supports MCP in two ways:

1. **ADK as MCP Client**

   * ADK agents consume tools exposed by an MCP server
2. **ADK as MCP Server**

   * ADK exposes its agents/tools so other clients/agents can call them

---

## 7) Tools & integrations beyond Python (11:57–12:33)

* Connect to GCP databases
* Use “hundreds of connectors” (as stated)
* Convert any API that follows **OpenAPI schema** into a tool

---

## 8) Authentication support (12:33–14:18)

Two modes:

1. Simple credentials

   * tokens/user/pass (store in secrets, not hardcoded)
2. Interactive auth (OAuth) flows supported

Flow described:

* user query → LLM decides tool needs auth
* tool triggers OAuth
* human completes auth (or automated flow)
* tool returns results → agent responds

Docs are referenced as having a dedicated auth section.

---

## 9) Evaluation (14:31–15:39)

Agent evaluation needs:

1. Evaluate **final answer** vs expected baseline
2. Evaluate **trace**:

   * which tools were called
   * tool outputs
   * whether correct tools were used

Mechanism:

* unit tests with example prompts + expected actions/results
* ADK runs comparisons and reports performance

---

## 10) Deployment options (15:39–16:22)

* Containerize and deploy anywhere
* On GCP:

  * **Vertex AI Agent Engine** (wrapper around Cloud Run) for quick deployment
  * Cloud Run for more control/flexibility
* On-prem supported (if you run containers yourself)

---

## 11) Memory (short-term and long-term) (16:29–17:40)

* **Short-term memory**:

  * session context across turns
  * agents share awareness of what happened in workflow
* **Long-term memory**:

  * persisted in storage/DB
  * user returns later → personalization from prior sessions

---

## 12) What demos will be shown (17:46–19:22)

1. Simple agent with a **weather tool**
2. **Bidirectional streaming** demo (Gemini Live-like)
3. “Data Science agent” sample:

   * connects to BigQuery
   * runs Python + SQL
   * can do RAG about BigQuery usage
   * trains models (BigQuery ML)

---

## 13) Tutorial walkthrough: build agents step-by-step (19:09–35:26)

### 13.1 Setup (19:29–20:01)

* Install ADK
* Install **LiteLLM** (so you can use multiple LLMs with less code)
* Goal: One app uses Gemini + GPT-4 + Claude (different agents)

### 13.2 Credentials (20:07–20:45)

* Uses API keys:

  * Google API key (Gemini via AI Studio)
  * OpenAI API key
  * Anthropic API key
* Alternative: use Vertex AI auth (project + location) instead of AI Studio key

Model choices shown:

* Gemini 2.0
* GPT-4o
* Claude 3

### 13.3 Tool definition: weather function (21:03–21:22)

* No real weather API; uses a mocked function:

  * only knows New York / London / Tokyo
  * unknown city → no answer

### 13.4 Create a basic agent (Gemini) with tools (21:28–22:25)

When defining an agent:

* Name (descriptive)
* Model
* Description (important for routing/selection)
* Instructions (behavior)
* Tools (the weather tool)

### 13.5 Session + Runner (22:32–23:22)

Important: memory is per session:

* define `user_id` and `session_id`
* runner orchestrates agent execution + state

### 13.6 Call the agent and inspect events (23:22–24:58)

* Events represent actions: tool calls, agent calls, etc.
* Test cities:

  * London → works
  * Paris → “no info”
  * New York → works

### 13.7 Same agent, different models (24:58–25:49)

Repeat with:

* GPT-4
* Claude

---

## 14) Multi-agent “team” example (25:49–27:41)

Agents:

1. Greeting agent (“hello”) — GPT-4
2. Weather agent — does weather tool
3. Farewell agent (“goodbye”)

Expected behavior:

* “Hello” triggers greeting tool
* weather question triggers weather agent/tool
* “Thanks” triggers goodbye tool

Result: routing works across multiple agents/tools.

---

## 15) Memory and shared state (27:47–32:48)

### 15.1 Two ways agents interact with state (28:06–29:12)

1. **Tool context state**

   * tools can read user preferences from session state
   * example: Celsius vs Fahrenheit preference
2. **Output key**

   * store an agent’s final result into session memory for later agents

### 15.2 Create in-memory session with initial state (29:19–29:51)

* Create new session with `user_id` + `session_id`
* Add initial preference:

  * temperature unit (Celsius/Fahrenheit)

### 15.3 Use state inside tool (30:27–30:51)

* Weather tool converts units based on stored preference

### 15.4 Store agent output with output_key (31:16–31:28)

* Root agent stores weather result in a key
* Later can read “last city checked” etc.

### 15.5 Demonstrated outcomes (31:57–32:48)

* London returned in Celsius when preference = Celsius
* New York returned in Fahrenheit when preference = Fahrenheit
* Output key captures the last result (“last city checked”)

---

## 16) Guardrails with callbacks (32:55–34:50)

### 16.1 Block requests based on keyword (before model call) (33:00–34:08)

* Callback blocks execution if user input contains a blocked word (example: “block”)
* Demo:

  * normal weather query works
  * “block the request” gets blocked

### 16.2 Block tool calls (before tool call) (34:13–34:50)

* Callback blocks tool call if tool input contains “Paris”
* Result:

  * request for Paris gets denied before tool executes

---

## 17) Demo 1: Built-in UI + bidirectional streaming (35:26–42:19)

### 17.1 Setup instructions (must keep) (35:40–37:15)

* Clone ADK samples from GitHub repo
* The creator posts code/links in Discord (video description)
* In `agent.py`:

  * defines a “search agent”
  * tools: **Google Search** + **built-in code execution**
* Model used: **Flash** (stated as currently the only one supporting streaming)
* Add credentials in `.env`

**How to run UI:**

* Go to the parent folder of the agent
* Run:

  * `adk web`
* This launches local UI with:

  * agent selection
  * events
  * state
  * artifacts
  * session
  * evaluation (unit tests)

### 17.2 Streaming test: Fibonacci (38:49–41:09)

Goal: prove you need code execution for correct answer.

* Ask agent: 500th Fibonacci first 10 digits

  * Without code tool → wrong answer
  * With code tool → correct answer
* Agent admits the mistake and explains discrepancy.

### 17.3 Google search tool test (41:27–42:00)

Prompt: find recent studies where Fibonacci used in research.
Agent response topic:

* Fibonacci in image compression
* mentions JPEG 2000 + Fibonacci codes

Key takeaway:

* minimal code to create agent with search + code execution
* UI helps validate behavior quickly

---

## 18) Demo 2: Data science multi-agent sample (42:27–51:27)

### 18.1 Capabilities shown

* Natural language → SQL queries to **BigQuery**
* Natural language → Python code for plots
* RAG to learn BigQuery / BigQuery ML syntax
* Tooling includes a “code interpreter” as a Vertex AI extension

### 18.2 Setup instructions (must keep) (44:19–45:46)

* Clone the data science agent sample
* Install dependencies
* Activate virtual environment
* Provide `.env` values:

  * GCP project name
  * location
  * credentials/auth
* Create BigQuery dataset and load sample data from Kaggle
* Run the provided Python script to load data + setup RAG + create extension if missing

Run UI:

* `adk web`

### 18.3 What data looks like (46:24–47:08)

* BigQuery dataset has `train` and `test` tables
* schema includes sales, country, store, product

### 18.4 Queries and traceability (47:13–48:26)

Example: “what countries exist?”

* Agent constructs SQL
* UI shows:

  * prompts/system instructions
  * tool calls
  * SQL created
* Response: Canada, Finland, Italy, Kenya, Norway, Singapore

### 18.5 Plot generation (48:34–49:34)

Prompt: “generate plot with total sales per country”
Flow:

* SQL query → fetch data
* Python tool executes plotting
* returns plot as artifact

### 18.6 BigQuery ML forecasting model (49:40–50:58)

Prompt: “What forecasting models can I train in BigQuery ML?”
Then: “Train a forecasting model using ARIMA_PLUS on train table to forecast sales”
Notes:

* It asks clarifying details (e.g., which column)
* Creator says he already tested earlier and it created a model

---

## 19) Wrap-up + outro (51:40–53:33)

* ADK is new and evolving fast
* encourages comments/questions
* ends with a short mindfulness prompt (“stop thinking; practice sensing”)

---
