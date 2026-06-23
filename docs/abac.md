> **Scope:** Extended roadmap and architecture discussion. **Implemented** ABAC engine behavior is summarized in [`specdoc.md`](../specdoc.md) (§12 ABAC) and [`admin-policies.md`](admin-policies.md). For file transfer architecture see [`transfers.md`](transfers.md).

Technical Report: Transitioning Aird from RBAC to Attribute-Based Access Control (ABAC)

1. Executive Context: Current Architectural Foundation

The architectural core of the Aird system (blinkerbit/aird) is engineered for high-performance, asynchronous file management across distributed networks. At its foundation, Aird utilizes the Python Tornado framework, leveraging non-blocking I/O and an asynchronous event loop to manage thousands of concurrent WebSocket connections. This is critical for maintaining real-time data streams and low-latency file operations without the resource exhaustion typical of thread-per-connection architectures.

A key performance differentiator in Aird is its aggressive use of memory-mapped file operations (mmap) for files exceeding 1 MB. By mapping files directly into the application's virtual address space, Aird bypasses traditional kernel-to-user-space buffer copies. From a security architecture perspective, this is a strategic advantage: mmap allows the system to perform direct address space scanning, enabling the access control layer to evaluate content-based "Resource Attributes"—such as the presence of PII or restricted keywords—at the memory level during the "Super Search" process.

The current security model relies on Role-Based Access Control (RBAC), primarily utilizing static "Admin" vs. "User" flags. While functional for local-first deployments, this binary approach lacks the context-aware granularity required for 2026 multi-tenant environments, leading to "role explosion" and high administrative friction.

Current Access Constraints by Feature

Aird Feature	Current Implementation Logic	Access Constraints
Super Search	WebSocket pattern matching via mmap	Binary Admin/User visibility of directory roots
Real-time Log Monitoring	WebSocket line-by-line streaming	Role-based permission to view system-level logs
P2P Transfers	WebRTC browser-to-browser exchange	General user permission for transfer room creation
Live Updating Shares	Token-based dynamic folder sharing	Admin-defined static shares or basic user tokens
Embedded SMB Server	Native SMB (Server Message Block)	Role-dependent mount permissions and R/W flags
Embedded WebDAV Server	WebDAV over HTTP with locking	User-level authentication for mapped network drives
Cloud Integration	Proxying to G-Drive / OneDrive	Individual account linking with role-based visibility

2. The Case for ABAC Integration

As we move toward 2026, the traditional "Admin" role represents a significant architectural liability. Ramping up security within Aird requires a transition to Attribute-Based Access Control (ABAC) to eliminate the "dual service cost"—where organizations pay separately for storage and delivery—by unifying both under a single, context-aware policy engine.

Access Control Paradigm Shift: RBAC vs. ABAC

Aspect	RBAC (Current)	ABAC (Proposed)
Logic Foundation	Users \rightarrow Roles \rightarrow Permissions	Attributes (User, Resource, Context) \rightarrow Logic
Granularity	Coarse; broad access per role	Ultra-fine; context-aware and specific
Scalability	Suffers from "role explosion"	Scales via dynamic logic evaluation
Contextual Awareness	Static; ignores Time, IP, or Device Health	Dynamic; evaluates real-time environmental data
Security Posture	Perimeter-based; binary	Zero-Trust; continuous verification

The Binary Trap: Why Boolean Flags Fail Multi-Tenant Isolation In enterprise environments, a simple "is_admin" flag is insufficient. If a high-privilege account is compromised, the lack of contextual verification allows for unrestricted lateral movement. ABAC solves this by requiring multi-dimensional validation.

Privilege Escalation Risks in Static RBAC Models Static roles cannot account for the "Principle of Least Privilege" in real-time. For Aird to be truly secure, administrative actions must require a "Super Admin Dual-Check," where high-risk operations (like modifying system-level feature flags) require both a role flag and a separate context-based boolean verification, such as proving the user is on a managed company device within a verified IP range.

3. Core ABAC Component Architecture

The transition requires the insertion of a dedicated ABAC engine into Aird's backend, structured around two primary components:

* Policy Decision Point (PDP): The logic engine that retrieves attributes for the User, Resource, and Environment to evaluate them against JSON-based security policies.
* Policy Enforcement Point (PEP): Integrated as asynchronous middleware within the Tornado request handlers. The PEP intercepts all API and WebSocket calls, querying the PDP before allowing any file data to be streamed or modified.

Aird's API-First architecture facilitates this transition, as the current JSON-based response system allows for the insertion of an attribute-evaluation layer without refactoring core file-handling logic.

Persistence Layer Evolution The current SQLite-backed persistence used for settings and share management will be evolved to store dynamic attribute definitions and logic policies. Using SQLite for local-first ABAC policy evaluation ensures that Aird maintains its "zero-configuration" speed while enabling the backend to query complex user dimensions (e.g., "Department," "Clearance") in real-time during session initialization.

4. Defining Attribute Dimensions for Aird

To enforce granular security, we define attributes across four dimensions:

* User Attributes:
  * LDAP-Synced Project Groups: Departmental memberships and job titles.
  * Security Clearance: (e.g., Public, Internal, PII-Authorized).
  * Storage Quotas: Dynamically evaluated maximum allowed storage.
* Resource Attributes:
  * Sensitivity Labels: AI-classified tags (Confidential, PII, Source Code).
  * Glob Pattern Filters: Dynamic path attributes (e.g., !tmp/*) used by the ABAC engine to filter file lists and search results in real-time.
  * File Metadata: Extension-based attributes (e.g., .log, .pdf).
* Action Attributes:
  * Operations: Read, Write, Delete, Execute.
  * Workflow Actions: Stream (logs), Share (generate token), and P2P_Transfer.
* Environment Attributes:
  * Contextual Data: Time of day (e.g., Working Hours 09:00-18:00), IP Range (Corporate VPN vs. Public).
  * Device Health: Managed vs. Unmanaged status, device cryptographic signature.

5. Context-Aware Security Policies & Implementation Logic

Aird will enforce "If-Then" logic that considers the "How" and "When" of access, specifically leveraging mmap scanning for content-aware decisions.

Policy 1: Time-Gated PII Access If User.Clearance == "PII-Authorized" and Resource.Contains("PII") Then Action.Read = "Permit" ONLY IF Environment.Time is between 09:00 and 18:00. Else "Deny" and log to Blockchain-backed Ledger.

Policy 2: Secure WebRTC P2P Transfer Thresholds If Action.Type == "P2P_Transfer" and Resource.Size > 2GB Then "Permit" ONLY IF Environment.Device == "Managed" and Environment.IP == "Corporate_Range". Rationale: Prevents massive data exfiltration over high-speed P2P channels on unmanaged public networks.

Policy 3: AI-Driven Automated Revocation If AI_Agent.Scout (Blink-style Docker Agent) classifies Resource as "Confidential" Then Share.PublicAccess = "Revoke" and Notify.Admin("Sensitive Data Leak Prevented"). Rationale: Automates data loss prevention using Docker-containerized AI agents that monitor file changes via mmap.

6. Frontend Integration: The User Experience of ABAC

Managing ABAC requires a sophisticated UI/UX that remains "user-friendly" despite underlying complexity. We will utilize Untitled UI React, specifically because it is built on React Aria. This foundation ensures that our complex attribute selectors and policy builders are WCAG-compliant and maintain high accessibility and longevity.


* Real-time Audit Evaluation: A glassmorphic feed showing live "Permit/Deny" decisions via WebSocket updates.
* Design-to-Code Alignment: Every visual attribute in the Untitled UI Figma system will map directly to Tailwind variables, ensuring the security state is visually unambiguous.

7. Zero-Trust Infrastructure and Security Hardening

ABAC is the mathematical enforcement mechanism for Aird's Zero-Trust posture. By assuming "never trust, always verify," the system ensures that the Principle of Least Privilege is enforced at the protocol level.

Aird Security Control Layers

Control Layer	Implementation Mechanism	Purpose
Password Hashing	bcrypt (Cost: 12)	Prevents brute-force on SQLite/PostgreSQL
Token Management	JWT (HS256)	HttpOnly/Secure session persistence
Multi-Factor Auth	TOTP (RFC 6238)	Mandatory for modifying ABAC attributes
File Encryption	AES-256-GCM	Client-side Zero-Knowledge encryption
Audit Integrity	Blockchain-backed Ledger	Cryptographic hash-chain of all access events

Mitigating Path Traversal and Insider Threats Granular attribute checks prevent Path Traversal by ensuring that the "Resource Path" attribute is always sanitized and validated against the user's "Home Directory" attribute. Furthermore, Insider Threats are mitigated because a role (e.g., "Manager") does not grant broad access; instead, the PEP checks environmental context (IP/Time) for every single request.

8. Strategic Implementation Roadmap
This is an excellent and highly viable approach for Aird. Using tags mapped via glob patterns fits perfectly into the 2026 architectural blueprints for moving to an Attribute-Based Access Control (ABAC) system.
Here is how your idea aligns with the sources and how you can implement it:
1. Using Tags as ABAC Resource Attributes In an ABAC system, access is granted by evaluating a combination of user attributes (e.g., job title), environment attributes (e.g., time of day or device type), and resource attributes
. The tags you create would function exactly as these resource attributes
.
For example, a file tagged as "Confidential" would carry that tag as its resource attribute. The ABAC security engine could then enforce a rule such as: Allow access to files tagged "Confidential" ONLY IF the user has a "Manager" role AND the request comes from a trusted corporate IP address during business hours
.
2. Mapping with Glob Patterns Your idea to map these tags using glob patterns is structurally sound because Aird already utilizes glob patterns (e.g., *.pdf, !tmp/*) for advanced filtering in its sharing architecture
. Repurposing Aird's existing pattern-matching engine to map security tags to files and directories would be a natural, high-performance extension of the current system
.
3. Building an Intuitive UI for Tagging To make the tag mapping process intuitive without cluttering the interface, you can leverage the 2026 UI/UX design trends recommended for Aird:
The Command Palette (Multimodal Interaction): Instead of making users click through nested settings to apply a glob pattern, you can use a Command Palette (triggered by shortcuts like Cmd+K). A user could simply type an intent like *"Tag all .pdf files in the Finance folder as Confidential," and the system would execute the mapping instantly
.
Liquid Glass Aesthetics: To visually display which files have which tags without overcrowding the screen, you can use "Liquid Glass" translucency
. This frosted-glass layering technique allows users to maintain context of the underlying file structure while viewing tags or settings overlaid on top
.
Spatial UI: You can also organize files based on physical "depth" in a 3D spatial interface, where highly sensitive (tagged) files are grouped or positioned differently based on the user's security clearance context
.
4. Future-Proofing: Adding AI Automation While allowing users to manually create and map tags via glob patterns is a great foundation, modern data security platforms are moving toward AI-driven automated classification
.
As you develop this feature, consider eventually integrating an AI agent that scans file content and automatically applies these tags (e.g., automatically tagging a file "PII" if it detects credit card numbers)
. This ensures that even if a user forgets to create a glob pattern for a specific file, the system will proactively label it and enforce your ABAC security rules automatically

