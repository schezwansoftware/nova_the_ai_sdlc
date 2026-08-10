# AI SDLC Platform — V1 Technical Architecture Specification

**Document Owner:** Atlas (AI Architect Agent)

**Target Audience:** Orion, Core, Nexus, Sage, Forge, Craft, Pixel, Sentinel, Aegis

**Status:** Approved for V1 Implementation (Updated with Stable Orchestrator Public API Contract)

---

## 1. Executive Summary

This document defines the production-grade V1 architecture for the enterprise **AI-Powered SDLC Automation Platform**.

The platform operates as an orchestrated multi-agent network designed to convert human product requirements into validated, production-ready GitHub Pull Requests, Confluence documentation, and structured SDLC state artifacts.

### Key Architectural Decisions

- **Human-in-the-Loop (HITL) Priority:** The initiator retains sole decision-making authority over requirements completion and milestone approvals. Agents analyze, recommend, and draft; humans authorize.
- **Hub-and-Spoke Orchestration:** Agents operate under strict isolation. **Direct agent-to-agent communication is forbidden.** The Orchestrator (built on LangGraph by Orion) manages all state transitions, interruptions, and data passing using strict JSON contracts.
- **Stable Public Orchestrator API Boundary:** Orion’s LangGraph graph implementation details, node names, and internal state structures are completely encapsulated behind a versioned, stable public API contract (`v1`). Clients (Core, Pixel, CLI) consume the public API exclusively.
- **Durable File-Based State Machine:** Workflow state is maintained as schema-validated JSON files in `.ai-sdlc/`. Conversational history is ephemeral and treated purely as transport context; file-based state is the single source of truth.
- **Decoupled AI Capability Abstraction:** Agents invoke abstract capabilities (e.g., `ReasoningCapability`, `CodingCapability`) rather than bound vendor APIs. Models are dynamically configurable and hot-swappable across OpenAI, Anthropic, and local/self-hosted instances.
- **File-Based Standards Layer for Org/Project Conventions:** Organization- and project-specific conventions (approved libraries, coding style, documentation format, UI/UX libraries, backend architecture, code review format) are captured as git-versioned `instructions.md` + domain-scoped `skills/*.md` files, resolved org → project and injected directly into agent prompts — not retrieved via RAG. Sage's RAG index is reserved for large, unstructured, or fast-changing knowledge (the codebase, Confluence, Jira) that can't be hand-curated into a file. See §9.1.
- **CLI-First with API Boundary:** V1 delivers a local CLI interfacing directly with the platform engine via a local JSON-RPC / REST contract over IPC/HTTP, guaranteeing full GUI compatibility without refactoring core orchestration logic.

---

## 2. Architecture Diagram

The system follows a tiered layer model separating interface, orchestration boundary, internal orchestration engine, agent isolation, capabilities, tool integration, and storage.

```mermaid
flowchart TD
    %% Clients
    subgraph ClientLayer["1. Client Layer"]
        CLI["CLI Client"]
        GUI["Future Web GUI (Pixel)"]
    end

    %% Engine & Orchestration
    subgraph EngineLayer["2. Engine & Core Platform Layer"]
        API["Platform API / IPC Server (Core)"]
        StateEngine["State & Schema Engine (Core)"]
    end

    subgraph OrchestrationBoundary["3. Orchestrator Public API Boundary (v1)"]
        OrchestratorAPI["Orchestrator Public API Facade"]
    end

    subgraph InternalOrchestration["4. Internal Orchestration Engine (Orion)"]
        LangGraphEngine["LangGraph Engine & Node Runner"]
        Checkpointer["LangGraph Checkpointer / State Sync"]
    end

    %% Agent Isolation Layer
    subgraph AgentLayer["5. Specialist Agent Layer"]
        PO["PO Agent"]
        UX["UX Agent"]
        Arch["Architecture Agent"]
        Dev["Developer Agent (Forge)"]
        Test["Testing Agent"]
        Sec["Security Agent"]
        Doc["Documentation Agent"]
    end

    %% Standards & Convention Context
    subgraph StandardsLayer["Standards & Convention Context Layer (Craft)"]
        StdResolver["Standards Resolver"]
        OrgStd["Org instructions.md / skills/*.md"]
        ProjStd["Project .ai-sdlc/standards/"]
    end

    %% Abstraction & Tooling
    subgraph CapabilityLayer["6. AI Capability Layer"]
        CapEngine["Capability Router & Guardrails"]
        Reasoning["Reasoning Capability"]
        Coding["Coding Capability"]
        Retrieval["Retrieval / RAG Capability (Sage)"]
    end

    subgraph ToolLayer["7. Tool & Integration Layer (Nexus)"]
        MCP["MCP Host Engine"]
        GitTool["Git / GitHub Adapter"]
        JiraTool["Jira Adapter"]
        ConfTool["Confluence Adapter"]
        FSTool["Filesystem & Terminal Adapter"]
    end

    %% Storage & External Systems
    subgraph StorageLayer["8. State & Storage"]
        StateStore[".ai-sdlc/ JSON State Store"]
        KnowledgeStore["Vector Store / RAG Index"]
    end

    subgraph ExternalSystems["9. External Systems"]
        GitHubExt["GitHub"]
        JiraExt["Jira"]
        ConfExt["Confluence"]
        LLMExt["AI Model Providers (OpenAI, Anthropic, Bedrock)"]
    end

    %% Communications
    CLI --> API
    GUI -.-> API
    API --> OrchestratorAPI
    OrchestratorAPI --> LangGraphEngine
    LangGraphEngine <--> Checkpointer
    Checkpointer <--> StateEngine
    StateEngine <--> StateStore

    LangGraphEngine <-->|JSON Contract| PO
    LangGraphEngine <-->|JSON Contract| UX
    LangGraphEngine <-->|JSON Contract| Arch
    LangGraphEngine <-->|JSON Contract| Dev
    LangGraphEngine <-->|JSON Contract| Test
    LangGraphEngine <-->|JSON Contract| Sec
    LangGraphEngine <-->|JSON Contract| Doc

    OrgStd --> StdResolver
    ProjStd --> StdResolver
    StdResolver -->|merged instruction context| PO & UX & Arch & Dev & Test & Sec & Doc

    PO & UX & Arch & Dev & Test & Sec & Doc --> CapEngine
    CapEngine --> Reasoning & Coding & Retrieval
    CapEngine <--> LLMExt

    Retrieval <--> KnowledgeStore

    PO & UX & Arch & Dev & Test & Sec & Doc --> MCP
    MCP --> GitTool & JiraTool & ConfTool & FSTool

    GitTool <--> GitHubExt
    JiraTool <--> JiraExt
    ConfTool <--> ConfExt

```

---

## 3. Component Architecture

| Component                            | Purpose                      | Primary Responsibilities                                                                                                                                                                                                      | Inputs                                             | Outputs                                            | Dependencies                 | Owner                      |
| ------------------------------------ | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- | -------------------------------------------------- | ---------------------------- | -------------------------- |
| **Orchestrator API Facade**          | Public API Contract Boundary | Exposes strict, versioned facade functions (`start_workflow`, `get_workflow_status`, `submit_clarification`, `submit_approval`, `resume_workflow`, `cancel_workflow`). Decouples external consumers from LangGraph internals. | Standardized Pydantic Request Models               | Standardized Pydantic Response / Error Models      | Core State Engine, Pydantic  | **Orion / Core Interface** |
| **Orchestrator Engine**              | SDLC State Machine           | Manages internal LangGraph nodes, state transitions, human interruptions, retries, and agent invocations.                                                                                                                     | Internal LangGraph commands, agent result payloads | Internal graph mutations, raw state checkpoints    | LangGraph, Core State Engine | **Orion**                  |
| **State & Schema Engine**            | State Integrity              | Manages reading/writing JSON state files, enforcing Pydantic schemas, concurrency locks, and state restoration.                                                                                                               | Raw JSON payloads, file read/write requests        | Schema-validated Pydantic models, JSON state files | `pydantic`, `filelock`       | **Core**                   |
| **Integration Adapter (MCP Engine)** | External Tool Gateway        | Exposes standardized tools (Git, Jira, Confluence, Filesystem) over Model Context Protocol (MCP) and native adapters.                                                                                                         | Tool call requests, integration credentials        | Standardized tool call execution results           | `mcp`, platform credentials  | **Nexus**                  |
| **Knowledge Engine**                 | Context Assembly             | Performs hybrid RAG search across codebase, Jira, and Confluence to inject precise context into agent prompts.                                                                                                                | Query strings, semantic context scope              | Context bundles, metadata-attributed code snippets | Vector DB, embedding models  | **Sage**                   |
| **Standards Resolver**               | Org/Project Convention Injection | Resolves and merges `instructions.md` + domain-scoped `skills/*.md` across platform → org → project scope; selects the agent-relevant subset and injects it into prompt assembly.                                          | Org config path, project `.ai-sdlc/standards/`, agent_id | Merged instruction context bundle             | Filesystem, Agent Factory    | **Craft**                  |
| **Developer Runtime**                | Code Generation & Execution  | Interfaces with repo files, executes local builds/tests via CLI, interacts with GitHub Copilot CLI, and manages Git branches.                                                                                                 | Code modification specs, test commands             | Patch files, execution outputs, PR URLs            | Git CLI, subshell execution  | **Forge**                  |
| **Agent Factory**                    | Specialist Instantiation     | Provides structural base classes and builders to generate specialist SDLC agents (PO, UX, Arch, Sec, Test).                                                                                                                   | Agent config, prompt templates, tools              | Executable specialist agent instances              | Capability Engine, Nexus     | **Craft**                  |
| **Client UI / CLI**                  | Human Interaction Layer      | Renders workflow status, formats artifact diffs, prompts for clarification, and captures milestone approvals via Public API endpoints.                                                                                        | User terminal input, Orchestrator IPC events       | User approval signals, text prompts                | `rich`, `typer`, Async IPC   | **Pixel**                  |
| **Evaluation Engine**                | Quality Gate & Testing       | Assesses agent execution quality, contract compliance, structural outputs, and platform regression suites.                                                                                                                    | Agent invocation logs, output JSONs                | Quality scores, contract pass/fail flags           | `pytest`, evaluation schemas | **Sentinel**               |
| **Security & Policy Guard**          | Policy & Secrets Enforcer    | Validates secret access, sanitizes LLM inputs/outputs against prompt injection, and enforces tool permissions.                                                                                                                | Inbound user prompts, outbound tool calls          | Sanitized payloads, policy decisions               | `pydantic-eval`, regex rules | **Aegis**                  |

---

## 4. Agent Architecture

All agents implement a single uniform abstract base interface. Agents are stateless across invocations; their operational state is entirely passed in via the runtime context and persisted back to `.ai-sdlc/`.

### Base Agent Abstract Interface

```python
from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from pydantic import BaseModel

InSchema = TypeVar("InSchema", bound=BaseModel)
OutSchema = TypeVar("OutSchema", bound=BaseModel)

class AgentResponse(BaseModel, Generic[OutSchema]):
    success: bool
    status_code: str  # e.g., "COMPLETED", "NEEDS_CLARIFICATION", "FAILED"
    data: OutSchema | None = None
    clarification_message: str | None = None
    error_message: str | None = None

class BaseAgent(ABC, Generic[InSchema, OutSchema]):
    agent_id: str
    version: str

    @abstractmethod
    async def execute(self, context: InSchema) -> AgentResponse[OutSchema]:
        """
        Executes the specialist agent logic.
        Read broadly (via tools/context), write narrowly (returns strictly typed output).
        """
        pass

```

### Agent Contract Example (PO Agent)

#### Input Contract (`POAgentInput`)

```json
{
  "workflow_id": "wf_123456789",
  "initiator": "harshit.bhatt@org.com",
  "raw_requirement": "Add support for Redis caching to our order service to reduce DB load under high traffic.",
  "project_context": {
    "repository_name": "order-service",
    "detected_tech_stack": ["Java", "Spring Boot", "Gradle"]
  },
  "previous_clarifications": []
}
```

#### Output Contract (`POAgentOutput`)

```json
{
  "success": true,
  "status_code": "COMPLETED",
  "data": {
    "feature_title": "Redis Cache Integration for Order Service",
    "summary": "Implement Redis-backed caching for order retrieval endpoints to drop database query latencies.",
    "functional_requirements": [
      "Cache 'GET /orders/{id}' responses in Redis with a configurable TTL (default 15 mins).",
      "Evict or update cache automatically upon 'PUT /orders/{id}' updates."
    ],
    "non_functional_requirements": [
      "Order retrieval response time under 50ms for cached hits.",
      "Graceful fallback to SQL database if Redis cluster is unreachable."
    ],
    "out_of_scope": [
      "Distributed session management",
      "Caching for user profile service"
    ],
    "acceptance_criteria": [
      "Cache hit ratio verified via metrics endpoint.",
      "Unit and integration tests pass with Redis embedded container."
    ]
  },
  "clarification_message": null,
  "error_message": null
}
```

---

## 5. Orchestrator Architecture & Public API Contract

### 5.1 Public vs. Internal Boundaries

To ensure Orion's internal implementation choices (LangGraph node structure, `Command(resume=...)` semantics, internal checkpoints) can evolve without breaking consumer services, Orion exports a strict **Public Orchestrator API Service Contract**.

```
┌─────────────────────────────────────────────────────────┐
│              Consumers (Core, Pixel, CLI)               │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼  [Public API Contract: v1]
┌─────────────────────────────────────────────────────────┐
│             Orchestrator Public API Facade              │
│ - start_workflow()       - submit_approval()            │
│ - get_workflow_status()  - resume_workflow()            │
│ - submit_clarification() - cancel_workflow()            │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼  [Internal Boundary]
┌─────────────────────────────────────────────────────────┐
│            Orion Internal Orchestration                 │
│ - LangGraph Graph Definition & State Sync               │
│ - Node Transitions & Conditional Routing Engine         │
│ - Checkpointer Mapping & Interrupt Command Translations │
└─────────────────────────────────────────────────────────┘

```

- **Public Boundary:** Consumers interact strictly with the Python interface or HTTP REST/IPC endpoints exposing `v1` schemas. Consumers never inspect LangGraph graph instances, node names, or internal execution frames directly.
- **Internal Boundary:** LangGraph state keys, raw node exceptions, memory checkpointers, and direct graph node handlers remain strictly private to Orion.

---

### 5.2 Public Orchestrator API Specification (`v1`)

#### Common API Data Models & Error Enums

```python
from enum import Enum
from typing import Generic, TypeVar, Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

T = TypeVar("T")

class WorkflowPhase(str, Enum):
    INIT = "INIT"
    REQUIREMENTS = "REQUIREMENTS"
    UX_DESIGN = "UX_DESIGN"
    ARCHITECTURE = "ARCHITECTURE"
    DEVELOPMENT = "DEVELOPMENT"
    TESTING = "TESTING"
    SECURITY = "SECURITY"
    CODE_REVIEW = "CODE_REVIEW"
    DOCUMENTATION = "DOCUMENTATION"
    PULL_REQUEST = "PULL_REQUEST"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class WorkflowStatusType(str, Enum):
    RUNNING = "RUNNING"
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class ErrorCode(str, Enum):
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED_INITIATOR = "UNAUTHORIZED_INITIATOR"
    INTERNAL_ORCHESTRATION_ERROR = "INTERNAL_ORCHESTRATION_ERROR"
    LOCK_ACQUISITION_FAILED = "LOCK_ACQUISITION_FAILED"

class APIErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class APIResponse(BaseModel, Generic[T]):
    api_version: str = "v1"
    success: bool
    data: Optional[T] = None
    error: Optional[APIErrorDetail] = None

```

---

#### 1. `start_workflow()`

**Purpose:** Initializes a new SDLC workflow instance, sets up file-based state tracing, and executes the initial workflow node.

- **Request Schema (`StartWorkflowRequest`):**

```python
class StartWorkflowRequest(BaseModel):
    initiator_id: str = Field(..., description="Email/ID of the user initiating the workflow.")
    raw_requirement: str = Field(..., min_length=10, description="Initial requirement text.")
    project_context: Dict[str, Any] = Field(default_factory=dict, description="Metadata regarding repo and stack.")

```

- **Response Schema (`StartWorkflowData`):**

```python
class StartWorkflowData(BaseModel):
    workflow_id: str
    initiator_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType
    created_at: datetime

```

- **Error Scenarios:**
- `VALIDATION_ERROR`: Empty requirement or invalid initiator format.
- `LOCK_ACQUISITION_FAILED`: Workspace state lock contention during initialization.

---

#### 2. `get_workflow_status()`

**Purpose:** Retrieves the current public status, phase execution tree, active interrupts, and available artifact references for a given workflow.

- **Request Schema (`GetWorkflowStatusRequest`):**

```python
class GetWorkflowStatusRequest(BaseModel):
    workflow_id: str

```

- **Response Schema (`WorkflowStatusData`):**

```python
class PendingAction(BaseModel):
    action_type: str  # "CLARIFICATION" or "APPROVAL"
    prompt_message: str
    target_phase: WorkflowPhase
    payload_artifact_path: Optional[str] = None  # e.g., ".ai-sdlc/requirements.json"

class WorkflowStatusData(BaseModel):
    workflow_id: str
    initiator_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType
    pending_action: Optional[PendingAction] = None
    updated_at: datetime
    artifacts: Dict[str, str]  # Maps artifact key -> relative file path

```

- **Error Scenarios:**
- `WORKFLOW_NOT_FOUND`: Workflow ID does not map to an existing state file.

---

#### 3. `submit_clarification()`

**Purpose:** Submits human answers to an active clarification question raised by an agent.

- **Request Schema (`SubmitClarificationRequest`):**

```python
class SubmitClarificationRequest(BaseModel):
    workflow_id: str
    initiator_id: str
    response_text: str = Field(..., min_length=1, description="Answer provided by the initiator.")

```

- **Response Schema (`SubmitClarificationData`):**

```python
class SubmitClarificationData(BaseModel):
    workflow_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType
    message: str = "Clarification accepted. Workflow resuming."

```

- **Error Scenarios:**
- `UNAUTHORIZED_INITIATOR`: `initiator_id` does not match the workflow owner.
- `INVALID_STATE_TRANSITION`: Workflow is not in `WAITING_FOR_CLARIFICATION` state.

---

#### 4. `submit_approval()`

**Purpose:** Records explicit human decision (Approval or Rejection) for major SDLC milestone phase transitions.

- **Request Schema (`SubmitApprovalRequest`):**

```python
class SubmitApprovalRequest(BaseModel):
    workflow_id: str
    initiator_id: str
    approved: bool
    feedback: Optional[str] = Field(None, description="Mandatory reason if approved=False.")

```

- **Response Schema (`SubmitApprovalData`):**

```python
class SubmitApprovalData(BaseModel):
    workflow_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType
    message: str

```

- **Error Scenarios:**
- `INVALID_STATE_TRANSITION`: Workflow is not in `WAITING_FOR_APPROVAL` state.
- `VALIDATION_ERROR`: Rejection submitted without explanatory feedback.

---

#### 5. `resume_workflow()`

**Purpose:** Resumes workflow execution following an explicit pause or system interruption (e.g., transient agent retry pause).

- **Request Schema (`ResumeWorkflowRequest`):**

```python
class ResumeWorkflowRequest(BaseModel):
    workflow_id: str
    initiator_id: str

```

- **Response Schema (`ResumeWorkflowData`):**

```python
class ResumeWorkflowData(BaseModel):
    workflow_id: str
    current_phase: WorkflowPhase
    status: WorkflowStatusType

```

- **Error Scenarios:**
- `INVALID_STATE_TRANSITION`: Workflow is already actively running or in terminal state (`COMPLETED`, `CANCELLED`).

---

#### 6. `cancel_workflow()`

**Purpose:** Aborts workflow execution immediately, transitions status to `CANCELLED`, and releases state locks.

- **Request Schema (`CancelWorkflowRequest`):**

```python
class CancelWorkflowRequest(BaseModel):
    workflow_id: str
    initiator_id: str
    reason: str

```

- **Response Schema (`CancelWorkflowData`):**

```python
class CancelWorkflowData(BaseModel):
    workflow_id: str
    status: WorkflowStatusType = WorkflowStatusType.CANCELLED
    cancelled_at: datetime

```

- **Error Scenarios:**
- `UNAUTHORIZED_INITIATOR`: Request submitted by user other than workflow initiator.
- `INVALID_STATE_TRANSITION`: Workflow is already in terminal state.

---

### 5.3 Public API Versioning Strategy

1. **Namespace isolation:** All API routes, Pydantic contracts, and RPC interfaces are strictly version-prefixed (`v1`).
2. **Backward Compatibility Guarantee:** Non-breaking field additions (optional fields) are permitted in minor updates. Breaking changes (removing fields, changing enum names) mandate bumping the route prefix to `v2`.
3. **Internal Decoupling:** Orion may refactor LangGraph state schemas, node graph topologies, or node implementation logic arbitrarily without bumping `v1`, provided all responses returned through the public facade conform strictly to the specified `APIResponse[T]` contracts.

---

## 6. State Architecture

State is decoupled from conversation history. The durable single source of truth is stored in `.ai-sdlc/` as JSON files conforming to Pydantic schemas managed by **Core**.

### State Directory Layout

```
.ai-sdlc/
├── workflow.json         # Orchestrator execution state & history
├── requirements.json     # PO Agent state
├── ux.json               # UX Agent state (wireframes, user flows)
├── architecture.json     # Technical Architecture Agent state
├── implementation.json   # Developer Agent state (changed files, patches)
├── testing.json          # Test suites and execution results
├── security.json         # Security audit & SAST scan results
└── pr.json               # Final Pull Request payload and links

```

### Primary Schemas

#### 1. `workflow.json` Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "workflow_id": { "type": "string" },
    "created_at": { "type": "string", "format": "date-time" },
    "updated_at": { "type": "string", "format": "date-time" },
    "current_phase": {
      "type": "string",
      "enum": [
        "INIT",
        "REQUIREMENTS",
        "UX_DESIGN",
        "ARCHITECTURE",
        "DEVELOPMENT",
        "TESTING",
        "SECURITY",
        "CODE_REVIEW",
        "DOCUMENTATION",
        "PULL_REQUEST",
        "COMPLETED",
        "FAILED",
        "CANCELLED"
      ]
    },
    "initiator": { "type": "string" },
    "active_interrupt": {
      "type": ["object", "null"],
      "properties": {
        "reason": {
          "type": "string",
          "enum": ["CLARIFICATION", "MILESTONE_APPROVAL"]
        },
        "message": { "type": "string" },
        "timestamp": { "type": "string", "format": "date-time" }
      }
    }
  },
  "required": [
    "workflow_id",
    "created_at",
    "updated_at",
    "current_phase",
    "initiator"
  ]
}
```

#### 2. `requirements.json` Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "version": { "type": "integer" },
    "approved_by_initiator": { "type": "boolean" },
    "feature_name": { "type": "string" },
    "user_stories": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": { "type": "string" },
          "story": { "type": "string" },
          "acceptance_criteria": {
            "type": "array",
            "items": { "type": "string" }
          }
        },
        "required": ["id", "story", "acceptance_criteria"]
      }
    }
  },
  "required": [
    "version",
    "approved_by_initiator",
    "feature_name",
    "user_stories"
  ]
}
```

#### 3. `architecture.json` Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "approved": { "type": "boolean" },
    "target_stack": {
      "type": "object",
      "properties": {
        "language": { "type": "string" },
        "framework": { "type": "string" },
        "build_tool": { "type": "string" }
      },
      "required": ["language", "framework", "build_tool"]
    },
    "component_changes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "component": { "type": "string" },
          "action": {
            "type": "string",
            "enum": ["MODIFY", "CREATE", "DELETE"]
          },
          "description": { "type": "string" }
        },
        "required": ["component", "action", "description"]
      }
    },
    "architectural_decisions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "title": { "type": "string" },
          "rationale": { "type": "string" }
        }
      }
    }
  },
  "required": ["approved", "target_stack", "component_changes"]
}
```

---

## 7. Tool & Integration Architecture

**Nexus** owns integration. To keep system design balanced, tools are integrated via **MCP (Model Context Protocol)** for multi-agent capabilities, alongside native SDK adapters for internal operations.

```
┌─────────────────────────────────────────────────────────────┐
│                      Specialist Agents                      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                      Nexus Tool Router                      │
├──────────────────────────────┬──────────────────────────────┤
│    MCP Servers (External)    │   Native Adapters (Local)    │
│  - GitHub MCP                │   - Subshell Command Exec    │
│  - Jira MCP                  │   - Local AST/File Reader    │
│  - Confluence MCP            │   - Copilot CLI Process Exec │
└──────────────────────────────┴──────────────────────────────┘

```

### Tool Categories

1. **GitHub Adapter:** Uses GitHub MCP for remote PR generation, branch locking, and issue sync. Uses native Git CLI locally for rapid branch creation, commits, and diff calculation.
2. **Jira Adapter:** MCP-backed tool providing issue creation, status transition (`In Progress`, `Under Review`), and comment updates.
3. **Confluence Adapter:** MCP-backed tool for auto-generating architecture decision records (ADRs) and feature design pages.
4. **Filesystem & Terminal Engine:** Native Python OS module with tight sandboxing (`chroot` lock to workspace root) ensuring agents cannot modify files outside target repo boundaries.

---

## 8. AI Capability Architecture

The system decouples **Agents**, **Capabilities**, and **LLM Models** to prevent vendor lock-in.

```
Agent Layer               PO Agent / Developer Agent / Security Agent
                                         │
                                         ▼
Capability Layer     [ Reasoning ]  [ Coding ]  [ Retrieval ]
                                         │
                                         ▼
Capability Router    Fallback & Guardrail Validation Layer
                                         │
                   ┌─────────────────────┼─────────────────────┐
                   ▼                     ▼                     ▼
Model Layer     Anthropic             OpenAI               Local / Ollama
              (Claude 3.5)          (GPT-4o)             (DeepSeek / Llama)

```

---

## 9. Knowledge Architecture (RAG)

**Sage** provides unified organizational knowledge retrieval through a dual-index architecture:

1. **Static AST & Repository Indexing:** Tree-Sitter processes target application codebases, producing semantic chunks linked to exact file/class paths.
2. **Enterprise Connectors:** Synchronizes documentation from Jira/Confluence.
3. **Context Injection:** When an agent invokes `RetrievalCapability`, Sage constructs a hybrid query (BM25 + Dense Embeddings), reranks results using a lightweight cross-encoder, and returns a tight token-budgeted Context Pack to the requesting agent.

---

## 9.1 Standards & Convention Context Layer

Org- and project-specific standards (approved libraries, coding style, documentation format, UI/UX libraries, backend architecture, code review format) are **curated and authored by humans**, unlike the codebase or Confluence — so they don't need semantic retrieval. **Craft** owns a lightweight, file-based, git-versioned **Standards Context Layer**, loaded directly into agent prompts rather than retrieved via RAG — the same convention proven by `CLAUDE.md`/skills-style files for coding agents.

### Layout

```
~/.ai-sdlc/org/                        # Org-wide, synced from a central org config repo
├── instructions.md                    # Hard rules: approved libraries, security policy, PR/review format
└── skills/
    ├── backend-architecture.md
    ├── ui-ux-libraries.md
    ├── code-review-format.md
    └── documentation-format.md

<target-repo>/.ai-sdlc/standards/      # Project-level, committed alongside the code
├── instructions.md                    # Project overrides/additions to org rules
└── skills/
    ├── backend-architecture.md        # e.g. "this service uses Spring Boot + Gradle, not the org default"
    └── ...

```

### Resolution Order

1. Platform defaults (built-in, minimal).
2. Org-level `instructions.md` + `skills/*.md`.
3. Project-level `instructions.md` + `skills/*.md` — narrowest scope wins on conflict.

At agent-instantiation time, the **Standards Resolver** merges these layers and hands **Agent Factory** only the `skills/*.md` file(s) relevant to the invoking agent's domain — e.g. the Architecture Agent receives `backend-architecture.md` + `instructions.md`, not `ui-ux-libraries.md` — keeping injected context small and on-topic. Because these files are authored and bounded in size, they are injected **in full**: no chunking, embedding, or retrieval-quality risk.

### Division of Labor vs. Sage RAG

| Source                                  | Mechanism                        | Use For                                                                                          |
| ---------------------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------- |
| Standards Context Layer (Craft)          | Direct file load, git-versioned   | Curated, authored standards: approved libraries, style guides, review/doc format, arch conventions |
| Knowledge Engine / RAG (Sage)            | Hybrid BM25 + Dense retrieval     | Large, unstructured, or fast-changing knowledge: the actual codebase, Confluence spaces, Jira history |

---

## 10. Security Architecture

**Aegis** establishes enterprise security guardrails across every layer:

- **Authentication & Authorization:** Local CLI binds to developer's GitHub/SSO session token. External tools (Jira, Confluence, GitHub) inherit access permissions via personal access tokens or OAuth credentials stored securely in system secret vaults (`keyring`).
- **Untrusted Code & Prompt Injection Protection:**
- All input from target application repositories (e.g., `README.md`, source code comments) is sanitized by Aegis before injection into LLM context windows.
- System prompts are strictly separated from user data frames inside JSON structures.

- **Tool Sandboxing:** Terminal command execution by **Forge** is strictly limited to an explicitly allowed command list (`git`, `mvn`, `gradle`, `npm`, `pytest`). Destructive commands (`rm -rf /`, raw network sockets, elevation commands like `sudo`) are explicitly blocked.
- **Secrets Redaction:** Regex mask filters intercept outbound agent execution trace logs, redacting API tokens, keys, and credentials before writing to `.ai-sdlc/` or stdout.

---

## 11. Repository Structure

The platform is structured as a modular Python package managed with `pyproject.toml`.

```
ai-sdlc-platform/
├── .github/
│   └── workflows/          # Platform CI/CD workflows
├── ai_sdlc/
│   ├── __init__.py
│   ├── cli/                # (Pixel) Typer-based CLI application
│   │   ├── main.py
│   │   ├── formatters.py
│   │   └── handlers.py
│   ├── engine/             # Core & Orion Orchestration Layer
│   │   ├── api/            # Public Orchestrator API (v1 Interface Contracts)
│   │   │   ├── facade.py   # Implementations of start_workflow, submit_approval, etc.
│   │   │   ├── schemas.py  # Public Pydantic Request/Response models
│   │   │   └── errors.py   # Public Error Enums and Detail models
│   │   ├── orchestrator.py # Internal LangGraph graph definitions (Orion Private)
│   │   ├── state.py        # Pydantic state schemas & file sync (Core)
│   │   └── ipc.py          # Local REST/IPC server interface (Core)
│   ├── agents/             # (Craft) Specialist Agents
│   │   ├── base.py         # Abstract Agent Interface
│   │   ├── po_agent.py
│   │   ├── ux_agent.py
│   │   ├── arch_agent.py
│   │   ├── dev_agent.py
│   │   ├── test_agent.py
│   │   └── sec_agent.py
│   ├── capabilities/       # Capability Layer & LLM Abstraction
│   │   ├── router.py
│   │   ├── reasoning.py
│   │   ├── coding.py
│   │   └── retrieval.py
│   ├── integrations/       # (Nexus) External Tools & MCP
│   │   ├── mcp_host.py
│   │   ├── github.py
│   │   ├── jira.py
│   │   └── confluence.py
│   ├── knowledge/          # (Sage) RAG & Context Engine
│   │   ├── indexer.py
│   │   └── retriever.py
│   ├── standards/          # (Craft) Standards Context Layer
│   │   ├── resolver.py     # Org -> project instructions.md + skills/*.md merge
│   │   └── loader.py
│   ├── security/           # (Aegis) Policy & Guardrails
│   │   ├── sanitizer.py
│   │   └── permissions.py
│   └── eval/               # (Sentinel) Agent QA & Evaluation
│       ├── test_contracts.py
│       └── eval_harness.py
├── tests/                  # Unit, Integration, Public API, & E2E tests
├── pyproject.toml
└── README.md

```

---

## 12. CLI Architecture

The V1 user experience is powered by a CLI built on `typer` and `rich`, invoking the **Orchestrator Public API Facade** exclusively.

### Commands

- `ai-sdlc init`: Initializes `.ai-sdlc/` state folder in target application repository.
- `ai-sdlc start --prompt "<requirement>"`: Calls `start_workflow()`.
- `ai-sdlc status`: Calls `get_workflow_status()` and displays visual pipeline.
- `ai-sdlc answer "<response>"`: Calls `submit_clarification()`.
- `ai-sdlc approve`: Calls `submit_approval(approved=True)`.
- `ai-sdlc reject --reason "<reason>"`: Calls `submit_approval(approved=False, feedback=reason)`.
- `ai-sdlc cancel`: Calls `cancel_workflow()`.

---

## 13. Future GUI Architecture

To guarantee zero core engine changes when introducing a Web GUI in V2, **Core** provides a lightweight FastAPI IPC daemon exposed over localhost (`[http://127.0.0.1:8765](http://127.0.0.1:8765)`), which routes requests directly to the Public Orchestrator API Facade (`/api/v1/orchestrator/*`).

---

## 14. Observability

All agent actions, LLM calls, tool executions, public API invocations, and state transitions are streamed to a central observability handler owned by **Core**.

---

## 15. Testing Strategy

1. **Public Contract Tests:** Validate that `start_workflow()`, `get_workflow_status()`, etc., maintain strict compliance with Pydantic `v1` schemas regardless of Orion internal graph changes.
2. **Unit Tests:** Core state parsing and JSON validation.
3. **Agent Evaluation Tests (Sentinel):** Enforce structural LLM outputs and code quality benchmarks.

---

## 16. MVP Scope

- **Must Have:** Public Orchestrator API (`v1`), CLI interface, Orion LangGraph runner, file-backed `.ai-sdlc/` state, PO/Arch/Dev/Testing agents, local Git + GitHub PR integration.
- **Should Have:** UX/Security/Documentation agents, Jira/Confluence MCP adapters, Standards Context Layer (org/project `instructions.md` + `skills/*.md` resolution).
- **Later:** Pixel Web GUI, multi-user workflow permissions.

---

## 17. Implementation Plan

```
Phase 1: Public API & State Foundation (Atlas / Core / Orion)
   ├── Implement Public Orchestrator API Schemas (v1)
   ├── Define Pydantic State Schemas (.ai-sdlc/)
   └── Connect Public Facade to Orion's LangGraph Execution Engine

Phase 2: Specialist Agents & Capabilities (Craft / Sage / Forge)
   ├── Implement PO Agent & Architecture Agent
   ├── Implement Standards Resolver (org/project instructions.md + skills/*.md)
   └── Implement Developer Agent (Forge) + Git CLI Sandbox

Phase 3: Security, QA & Integration (Aegis / Sentinel / Nexus)
   └── Integrate GitHub PR API Adapter & Aegis Sanitizer

```

---

## 18. Architectural Decisions and Alternatives

### Decision 1: Explicit Public API Facade for Orchestration

- **Reason:** Encapsulates Orion's internal LangGraph implementation, preventing tight coupling between Core, Pixel, CLI, and LangGraph internals.
- **Alternatives Rejected:** Direct consumption of LangGraph graph object by CLI/IPC handlers (rejected due to severe breakages whenever graph nodes or internal state schemas are modified).

### Decision 2: Durable JSON State Files (`.ai-sdlc/`)

- **Reason:** Plain, schema-validated JSON files are human-readable, trackable via Git, easy to debug, transparent to developers, and require no complex database setup.

---

## 19. Risks and Mitigation Strategies

| Identified Risk                                                                   | Severity | Mitigation Strategy                                                                                                                                           |
| --------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **API Boundary Leakage** (Internal LangGraph exceptions leaking to public caller) | High     | Public Facade catches all internal execution errors and translates them into structured `APIErrorDetail` responses with `INTERNAL_ORCHESTRATION_ERROR` codes. |
| **Agent Infinite Loops**                                                          | High     | Orion enforces a strict retry limit per phase. When exceeded, the Public API reports `WAITING_FOR_CLARIFICATION` or `FAILED` status.                          |

---

## 20. Open Questions

1. **Jira Lifecycle Policy:** Should the platform automatically transition existing Jira tickets during workflow progress, or only create/comment on tickets upon explicit human approval?
2. **Copilot CLI Integration Scope:** Should **Forge** interact with GitHub Copilot strictly via Copilot CLI subprocess calls, or fall back to direct LLM provider API invocations when Copilot CLI is unavailable locally?

---
