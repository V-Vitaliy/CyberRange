AI Security & Alignment CyberRange
==================================

Overview
--------

**AI Security CyberRange** is an isolated, Vulnerable-by-Design laboratory platform created for threat modeling and training in the defense of modern AI applications (LLMs).

Unlike traditional "black-box" testing via external APIs, this platform provides a fully controlled On-Premise or Hybrid Cloud infrastructure. Students and cybersecurity professionals can explore, attack, and defend systems against threats outlined in standards like the **OWASP Top 10 for LLM Applications**.

Game Modes (Red vs. Blue)
-------------------------

The platform supports an interactive, competitive process:

### Red Team (Offense)

Players attack the platform's vulnerable endpoints using modern attack vectors:

*   **Indirect Prompt Injection:** Manipulation of the LLM context.
    
*   **RAG Data Poisoning:** Uploading malicious PDF documents into the vector database via a **Path Traversal** vulnerability.
    
*   **SQL Injection (SQLi):** Exploiting vulnerabilities in chat history (bypassing the ORM).
    
*   **JWT Signature Bypass:** Authentication bypass using the alg: none technique.
    
*   **Sponge Bombs & DoS:** Exhaustion of GPU computational resources (intercepted by an architectural queue and a Redis Rate Limiter).
    

### Blue Team (Defense / SOC)

The defense team uses a **Forensics-Driven Defense** model.

*   **Log Analysis:** Defenders investigate incidents by analyzing JSONB logs from the SIEM system.
    
*   **Defense Economy:** For successful investigations, the team earns "defense points" (Defense Budget).
    
*   **Dynamic Patching:** Points can be spent to activate defenses **in real-time** (without server reboots):
    
    *   Activating a **Cross-Encoder Reranker** (Filtering poisoned RAG context).
        
    *   Overriding the **System Prompt** with a stricter one.
        
    *   Enabling **Redis Rate Limiting** (DoS attack mitigation).
        
    *   Enabling strict **JWT** cryptographic validation.
        

Architecture (Hybrid Cloud / On-Premise)
----------------------------------------

To ensure maximum realism and optimize costs (FinOps), the architecture is divided into two compute nodes:

*   **State Server (CPU):** A lightweight node for state storage.
    
    *   _PostgreSQL:_ Stores users, SIEM logs, CTF flags, and economy balance.
        
    *   _Redis:_ Session caching and Token Bucket for Rate Limiting.
        
*   **AI Compute Server (GPU-Accelerated):** Heavy computation node.
    
    *   _FastAPI:_ The core of the platform (asynchronous API).
        
    *   _In-Process LLM:_ Meta Llama-3 (8B) running directly in the backend process VRAM via llama-cpp-python (with Q4\_K\_M quantization and Flash Attention).
        
    *   _ChromaDB & MinIO:_ Vector storage and an S3-compatible sandbox.
        
    *   _ETL Worker:_ Background tasks based on nltk and tiktoken for PDF chunking.
        

Development Roadmap (Sprints)
-----------------------------

The project is divided into 5 main sprints. Current status:

*   \[x\] **Sprint 1: Core Infrastructure & In-Process LLM** (Docker, FastAPI, Llama-3 init, Async Queues).
    
*   \[x\] **Sprint 2: Vulnerable API & Red Team Vectors** (SQLi, Path Traversal, ChromaDB, RAG Poisoning, JWT Bypass).
    
*   \[x\] **Sprint 3: Defense API & Blue Team Economy** (Dynamic Patching, Reranker, Redis Rate Limiting, API Customization).
    
*   \[ \] **Sprint 4: Frontend UI / UX** (Next.js Chat, Blue Team Dashboard, WebSocket ETL Status).
    
*   \[ \] **Sprint 5: Forensics, CLI Admin & Golden Data** (Auto-grading, SIEM Engine Logging, Teardown scripts).
