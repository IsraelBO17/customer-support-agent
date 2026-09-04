# Reflection

## Design Decision

One deliberate design decision was splitting memory into two independent systems instead of one. Long-term memory (`MemoryHook`) does semantic retrieval — pulling relevant facts from past sessions and prepending them to the user's message. For session-level continuity, I chose AWS's `AgentCoreMemorySessionManager` over the course's reference hand-rolled hook, because it restores actual prior messages into the conversation history rather than appending a text summary into the system prompt, so the model sees real prior turns instead of a paraphrase it has to reinterpret. The two systems write to separate memory resources and never conflict, keeping each concern isolated.

## Challenge

The harder challenge wasn't writing the agent, it was getting it running in AWS — every fix revealed a hidden next layer. First, the `agentcore` CLI already on my machine turned out to be AWS's newer, incompatible tool with an entirely different project model — I had to identify the mismatch and install the correct, deprecated toolkit that actually matched my code. Then came a string of IAM permission gaps: my auto-created execution role only covered resources it created itself, so anything I referenced by ID (a pre-existing Memory resource or Knowledge Base) failed until I traced each failure through CloudWatch logs and attached scoped policies one at a time. Trickiest was the browser tool, which failed with no visible error at all, until I found the real exception was logged at DEBUG level while my root logger was set to WARNING, silently swallowing it. Raising just that one logger finally surfaced the actual cause.

## Production Extension

For production, two gaps stand out most. Security: the agent trusts a bare `customer_id` in the payload with no verification, so anyone could claim to be any customer and access their orders or trigger refunds. I'd add real authentication, like a signed session token tied to a verified login, before any tool touches customer data. Reliability: even with a working knowledge base tool, I saw the model occasionally fabricate a plausible-sounding but incorrect answer instead of admitting uncertainty. I'd add an automated eval layer that checks tool-grounded claims against the actual tool output before a response ships, rather than relying on the system prompt alone to prevent hallucination.
