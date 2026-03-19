# Agentic Expansion Roadmap: Matrix Wellness Monitor to Activist Intelligence Assistant

## 1. Vision & Overview

The current Matrix Wellness Monitor is a deterministic, rule-based "Dead Man's Switch." It executes a rigid state machine: monitor heartbeat, check OSINT, escalate, and release data. 

The next evolution transforms this bot into an **Agentic Activist Intelligence Assistant**. By integrating an LLM-powered agent framework (inspired by OpenClaw/ZeroClaw) and connecting it to a secure knowledge base (via `notebooklm-py`), the bot will transition from a passive monitor to an active, context-aware collaborator. It will be able to synthesize group chat history, query deep research archives, and autonomously generate actionable outputs like FOIA requests, legal summaries, or threat models.

## 2. Core Architectural Components

To achieve this without compromising the strict security and privacy requirements of activist infrastructure, the architecture must be modular and local-first.

### 2.1 The Agent Framework: ZeroClaw
While OpenClaw is a popular agent framework, **ZeroClaw** is the recommended choice for this expansion. ZeroClaw is a Rust-based, ultra-lightweight alternative that runs in a <5MB memory footprint and starts in <10ms [1]. 
- **Why ZeroClaw?** Activist infrastructure is often hosted on low-cost, low-resource VPS instances (e.g., $5/month servers). ZeroClaw's minimal overhead ensures the agent can run alongside the Matrix bot without requiring expensive cloud GPUs or massive RAM allocations.
- **Local-First Security:** ZeroClaw supports local LLMs (via Ollama or vLLM) and OpenAI-compatible endpoints, ensuring sensitive chat context never has to leave the server if a local model is used.

### 2.2 The Knowledge Base: NotebookLM via `notebooklm-py`
Google's NotebookLM is a powerful tool for grounding LLMs in specific documents (RAG - Retrieval-Augmented Generation). The `notebooklm-py` library provides an unofficial Python API to programmatically interact with NotebookLM [2].
- **Integration:** The agent will use `notebooklm-py` as a "Skill" or "Tool." When a user asks a complex question in the Matrix chat, the agent can query a specific NotebookLM archive containing legal precedents, historical case files, or activist training manuals.
- **Capabilities:** The agent can upload new documents (e.g., a leaked PDF dropped in the Matrix chat) to the notebook, ask questions against the corpus, and even generate audio overviews or study guides for the group.

### 2.3 The Matrix Interface
The existing `matrix-nio` / `simplematrixbotlib` framework remains the communication layer. However, instead of just listening for `!commands`, it will route natural language messages to the ZeroClaw agent when explicitly invoked (e.g., `@bot, can you summarize the latest updates on the pipeline protest?`).

## 3. Agentic Capabilities & Use Cases

Once the LLM and tools are integrated, the bot can perform complex, multi-step workflows.

### 3.1 Context-Aware FOIA Generation
**Scenario:** The group is discussing a recent police action in the chat.
**Workflow:**
1. A user tags the bot: `@bot, draft a FOIA request to the local PD regarding the arrests at the 5th Street protest yesterday.`
2. The agent reads the recent chat history to extract context (date, location, involved agencies).
3. The agent queries the NotebookLM archive (via `notebooklm-py`) for "standard FOIA templates for police records in this state."
4. The agent drafts the FOIA request, fills in the contextual details, and posts the draft back to the Matrix chat for review.

### 3.2 Automated Threat Modeling
**Scenario:** A user drops a link to a new surveillance technology being deployed in their city.
**Workflow:**
1. The agent detects the link and uses a web-scraping tool to read the article.
2. It cross-references the technology against a NotebookLM archive of known surveillance tech and countermeasures.
3. It generates a brief threat model and mitigation strategy, posting it to the group.

### 3.3 Intelligent Emergency Data Synthesis
**Scenario:** The Dead Man's Switch is activated.
**Workflow:**
Instead of just dumping raw emergency data, the agent can synthesize the user's recent chat history, their OSINT footprint, and their emergency data to provide the group with a coherent "Last Known Status" report, highlighting the most likely investigative leads.

## 4. Implementation Roadmap

### Phase 1: LLM Integration & Natural Language Routing
- **Objective:** Allow the bot to respond to natural language queries.
- **Tasks:**
  - Integrate a local LLM (e.g., Llama 3 via Ollama) or a secure API provider.
  - Update the Matrix bot to route messages starting with `@bot` to the LLM.
  - Implement basic conversation memory (storing recent chat context in a sliding window).

### Phase 2: ZeroClaw Agent Framework & Tooling
- **Objective:** Give the LLM the ability to take actions.
- **Tasks:**
  - Deploy the ZeroClaw runtime alongside the Python bot.
  - Define the bot's "Skills" (Tools): Web Search (SerpAPI), Matrix Chat History Retrieval, and Document Drafting.
  - Establish the communication bridge between the Python Matrix bot and the Rust ZeroClaw agent.

### Phase 3: NotebookLM Integration
- **Objective:** Ground the agent in a secure, curated knowledge base.
- **Tasks:**
  - Install and configure `notebooklm-py`.
  - Create a dedicated NotebookLM instance for the activist group's archives.
  - Build a ZeroClaw tool that allows the agent to execute `notebooklm ask` and `notebooklm source add` commands.

### Phase 4: Advanced Workflows & Security Hardening
- **Objective:** Implement specific activist workflows (like FOIA generation) and ensure strict data boundaries.
- **Tasks:**
  - Write system prompts and agent instructions for specific tasks (FOIA, legal summaries).
  - Implement strict isolation: Ensure the agent *cannot* access the encrypted Emergency Data Vault unless the consensus threshold is met.
  - Audit the prompt injection surface area to prevent malicious actors from tricking the bot into revealing sensitive group info.

## 5. Security Considerations for Agentic Expansion

Adding an LLM introduces new security risks that must be mitigated:
- **Prompt Injection:** Malicious users could attempt to extract the bot's system prompt or trick it into executing unauthorized tools. ZeroClaw's strict sandboxing and explicit allowlists are critical here.
- **Data Leakage:** If using a cloud LLM provider (like OpenAI or Anthropic), chat context is sent to third-party servers. For high-risk activist groups, **local models (LocalLLM) are mandatory**.
- **NotebookLM Privacy:** Google NotebookLM is a cloud service. While `notebooklm-py` provides API access, the documents are still hosted by Google. Highly sensitive, non-public documents should *not* be uploaded to NotebookLM; instead, a local RAG solution (like ChromaDB + local embeddings) should be used for classified internal data.

## References
[1] ZeroClaw GitHub Repository. https://github.com/zeroclaw-labs/zeroclaw
[2] notebooklm-py GitHub Repository. https://github.com/teng-lin/notebooklm-py
