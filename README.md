# AI Security & Alignment CyberRange

**Overview**

AI Security CyberRange is a vulnerable-by-design, isolated laboratory environment engineered for modern AI threat modeling and defense.

Unlike traditional "black-box" testing via external APIs, this platform provides a fully controlled On-Premise (or isolated Hybrid Cloud) infrastructure where students and cybersecurity professionals can analyze, attack, and mitigate the latest threats defined in standards like the OWASP Top 10 for LLM Applications.

*Game Modes (Red vs. Blue)*

The platform supports a highly engaging, competitive learning environment:

🟥 Red Team (Offense): Exploits realistic UX/UI elements. Vectors include Indirect Prompt Injection, RAG Data Poisoning (via Path Traversal / S3 manipulation), SQL Injection in chat history, and Context Window Overflow.

🟦 Blue Team (Defense / SOC): Operates on a Forensics-Driven Defense model. Analyzes deep digital traces (JSONB SIEM logs, Cosine Similarity anomalies) to identify attacks. Uses a "Defense Economy" to purchase and deploy live mitigations (e.g., Cross-Encoder Rerankers, Redis Rate Limiting, strict JWT validation) without halting the service.

 **Architecture (Hybrid Cloud / On-Premise)**

To ensure extreme realism (including Denial of Service tests on the LLM) without breaching public cloud TOS or incurring massive costs, the system uses a highly optimized architecture:

In-Process LLM: Meta Llama-3 (8B) loaded directly into the VRAM of the main backend process using llama-cpp-python with Q4_K_M quantization and Flash Attention.

Hardware Isolation: Background ETL processes (PDF Chunking & Vectorization via mpnet) run strictly on the CPU, ensuring that malicious file uploads do not crash the GPU-bound chat inference.

*State & Compute Split:* 

State Server: PostgreSQL (SIEM logs, users), Redis (Rate Limiting).

Compute Server: FastAPI, ChromaDB (Vectors), MinIO (S3 Sandbox).

**Development Roadmap (Backlog)**

The project is currently in active development, broken down into 5 main Sprints:

[x] Sprint 1: Core Infrastructure & In-Process LLM (Docker, FastAPI, Llama-3 init).

[ ] Sprint 2: Vulnerable API & Red Team Vectors (SQLi, RAG Poisoning, JWT Bypass).

[ ] Sprint 3: Defense API & Blue Team Economy (SIEM, Reranker, Rate Limiting).

[ ] Sprint 4: Frontend UI / UX (Next.js Chat, Blue Team Dashboard).

[ ] Sprint 5: Forensics, CLI Admin & Golden Data (Auto-grading, Teardown scripts).
