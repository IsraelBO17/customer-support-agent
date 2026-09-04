"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the step-by-step solution files and INSTRUCTIONS.md for guidance.
Do NOT copy the solution directly — work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

# strands_tools' browser module logs its actual tool-call exceptions at DEBUG
# level (see strands_tools/browser/browser.py), which the WARNING root level
# above silently swallows. Raise just this logger so browser failures are
# actually visible in CloudWatch instead of only reaching the model as text.
logging.getLogger("strands_tools.browser").setLevel(logging.DEBUG)

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()

# TODO: Create the BedrockAgentCoreApp instance
app = BedrockAgentCoreApp()


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in Part 1 of the INSTRUCTIONS.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://customersupportgateway-icflya0658.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = "NBRBHNINWA"
REGION      = "us-east-1"
MEMORY_ID   = "CustomerSupportMemory-rrdFqzHTWj"


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Create:
#   1. A BedrockModel using model_id "global.amazon.nova-2-lite-v1:0"
#   2. A MemoryClient with region_name=REGION
#   3. A boto3 client for the "bedrock-agent-runtime" service in REGION
#
# Hint: model = BedrockModel(model_id=model_id)

model_id = "global.amazon.nova-2-lite-v1:0"

# TODO: Create the BedrockModel instance
model = BedrockModel(model_id=model_id)

# TODO: Create the MemoryClient instance
memory_client = MemoryClient(region_name=REGION)

# TODO: Create the boto3 bedrock-agent-runtime client
_bedrock_runtime = boto3.client('bedrock-agent-runtime', region_name=REGION)


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Return a dict: { strategy["type"]: strategy["namespaces"][0] for each strategy }
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    strategies = mem_client.get_memory_strategies(memory_id)
    return {s["type"]: s["namespaces"][0] for s in strategies}


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        # TODO: Store actor_id, session_id, memory_id, memory_client as attributes
        # TODO: Call get_namespaces() and store the result as self.namespaces
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.namespaces = get_namespaces(self.memory_client, self.memory_id)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        # TODO: Implement memory retrieval
        # Steps:
        #   1. Get the last message from event.agent.messages
        #   2. Check it is a user message and not a tool result
        #   3. Extract the user query text
        #   4. For each namespace in self.namespaces, call retrieve_memories()
        #   5. Collect non-empty memory texts with strategy type tags
        #   6. If any found, prepend them to the user message
        actor_id = event.agent.state.get("actor_id")
        if not actor_id:
            return

        messages = event.agent.messages
        if (
            not messages
            or messages[-1]["role"] != "user"
            or "toolResult" in messages[-1]["content"][0]
        ):
            return

        user_query = messages[-1]["content"][0]["text"]

        try:
            all_context = []
            for strategy_type, namespace in self.namespaces.items():
                resolved_namespace = namespace.format(actorId=actor_id)
                memories = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=resolved_namespace,
                    query=user_query,
                    top_k=5,
                )
                for memory in memories:
                    if isinstance(memory, dict):
                        text = memory.get("content", {}).get("text", "").strip()
                        if text:
                            all_context.append(f"[{strategy_type}] {text}")

            if all_context:
                context_block = "\n".join(all_context)
                original_text = messages[-1]["content"][0]["text"]
                messages[-1]["content"][0]["text"] = (
                    f"Customer Context:\n{context_block}\n\n{original_text}"
                )
                logger.info("Retrieved %d memory items for actor %s", len(all_context), actor_id)

        except Exception as exc:
            logger.error("Failed to retrieve customer context: %s", exc)

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        # TODO: Implement memory saving
        # Steps:
        #   1. Get messages from event.agent.messages
        #   2. Walk backwards to find the last user query (plain text)
        #      and the last assistant response
        #   3. Call memory_client.create_event() with both messages
        actor_id   = event.agent.state.get("actor_id")
        session_id = event.agent.state.get("session_id")
        if not actor_id or not session_id:
            return

        try:
            messages = event.agent.messages
            user_text = agent_text = None

            for msg in reversed(messages):
                if msg["role"] == "assistant" and not agent_text:
                    content = msg["content"]
                    if isinstance(content, list):
                        agent_text = content[0].get("text", "")
                    else:
                        agent_text = str(content)
                elif (
                    msg["role"] == "user"
                    and not user_text
                    and "toolResult" not in msg["content"][0]
                ):
                    user_text = msg["content"][0]["text"]
                    break

            if user_text and agent_text:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    messages=[
                        (user_text, "USER"),
                        (agent_text, "ASSISTANT"),
                    ],
                )
                logger.info("Saved interaction to memory for actor %s", actor_id)

        except Exception as exc:
            logger.error("Failed to save interaction: %s", exc)

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        # TODO: Register retrieve_customer_context on MessageAddedEvent
        # TODO: Register save_support_interaction on AfterInvocationEvent
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    # TODO: Implement the Knowledge Base search
    if not KB_ID: 
        return "Knowledge base not configured."
    resp = _bedrock_runtime.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": query},
    )
    results = resp.get("retrievalResults", [])
    if not results:
        return f"No information found for: {query}"

    chunks = [r["content"]["text"] for r in results]
    return "\n---\n".join(chunks)


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    # TODO: Build the code string (use an f-string to inject the arguments)
    code = f"""
import json

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

loyalty_points = {loyalty_points}
tier = "{tier}"
order_total = {order_total}
product_category = "{product_category}"

POINT_VALUE = 0.01  # dollar value of a single loyalty point

# Redeem points in blocks of 500, capped at 50% of the order value
points_floor = (loyalty_points // 500) * 500
max_redeemable_value = order_total * 0.5
max_redeemable_points = int(max_redeemable_value / POINT_VALUE)
max_redeemable_points = (max_redeemable_points // 500) * 500
points_redeemed = min(points_floor, max_redeemable_points)
points_discount_value = points_redeemed * POINT_VALUE

subtotal_after_points = order_total - points_discount_value

tier_discount_rate = tier_rates.get(tier, 0.0)
tier_discount_value = subtotal_after_points * tier_discount_rate

final_total = subtotal_after_points - tier_discount_value
total_savings = order_total - final_total

points_earn_rate = earn_rates.get(product_category, earn_rates["standard"])
points_earned = int(final_total * points_earn_rate)

remaining_points = loyalty_points - points_redeemed + points_earned

result = {{
    "order_total": round(order_total, 2),
    "points_redeemed": points_redeemed,
    "points_discount_value": round(points_discount_value, 2),
    "tier": tier,
    "tier_discount_rate": tier_discount_rate,
    "tier_discount_value": round(tier_discount_value, 2),
    "final_total": round(final_total, 2),
    "total_savings": round(total_savings, 2),
    "points_earned": points_earned,
    "remaining_points": remaining_points,
}}
print(json.dumps(result))
"""

    try:
        # TODO: Execute the code using code_session and return the result
        with code_session(REGION) as code_client:
            response = code_client.invoke("executeCode", {
                "code": code,
                "language": "python",
                "clearContext": True,
            })
        for event in response["stream"]:
            return json.dumps(event["result"])

    except Exception as e:
        # TODO: Implement fallback calculation using tier discount only
        logger.error("Code Interpreter unavailable, falling back to tier-only discount: %s", e)

        tier_rates = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
        tier_discount_rate = tier_rates.get(tier, 0.0)
        tier_discount_value = order_total * tier_discount_rate
        final_total = order_total - tier_discount_value

        fallback_result = {
            "order_total": round(order_total, 2),
            "tier": tier,
            "tier_discount_rate": tier_discount_rate,
            "tier_discount_value": round(tier_discount_value, 2),
            "final_total": round(final_total, 2),
            "total_savings": round(tier_discount_value, 2),
            "note": "Fallback calculation — Code Interpreter unavailable, points not applied.",
        }
        return json.dumps(fallback_result)


# ── System Prompt ─────────────────────────────────────────────────────────────
# Guides the agent on its role, available tools, and how to use them.

SYSTEM_PROMPT = """You are a customer support assistant for an online retail platform. You help customers track orders, process returns and refunds, answer product and policy questions, and calculate loyalty rewards discounts, while remembering relevant details from earlier conversations with the same customer.

You have access to the following tools. Always use a tool to get real data instead of guessing or making up details about orders, products, refunds, or loyalty balances.

- search_knowledge_base: product specifications, warranty terms, return and refund policy, the loyalty rewards program rules, and order status definitions. Use this for any policy or product question.
- Order lookup tools (from the Gateway): look up a specific order by ID, list all orders for a customer, or fetch a customer's profile (name, loyalty tier, and points balance). Use these before answering questions about order status, tracking, or a customer's current loyalty standing.
- Refund tools (from the Gateway): initiate_refund starts and approves a refund for an order, check_refund_status looks up an existing refund, and get_return_label generates a prepaid return shipping label. Only initiate a refund after confirming the order is eligible per the return policy.
- calculate_loyalty_discount: computes the exact discount for an order, combining points redemption and tier discount. Always use this tool for loyalty math instead of calculating it yourself; look up the customer's current tier and points balance first if they weren't given to you.
- A web browser tool, for the rare case where a customer's question needs information from a live webpage that none of the tools above can answer. Prefer the tools above whenever they apply.

Guidelines:
- Ask the customer for their order ID or customer ID if you need it and it hasn't been provided or established earlier in the conversation.
- Summarize tool results in clear, natural language. Never show raw JSON or internal tool/field names to the customer.
- For multi-step requests (for example, "I want to return this order"), work through the steps in order: check the order and its eligibility against policy, then take the appropriate action (refund, return label, etc.), confirming with the customer along the way if the request is ambiguous.
- If earlier context about this customer is available, use it naturally to personalize your response, but don't recite it verbatim.
- Keep responses concise and friendly, like a helpful support agent. A few sentences is usually enough, unless the request calls for a detailed breakdown, such as a discount calculation.
- If a request is outside what your tools can do (for example, changing account security settings or a payment method), say so clearly and suggest the customer contact a human support agent rather than guessing or improvising.
"""


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    # TODO: Implement the agent invocation
    user_input = payload.get("prompt")
    actor_id = payload.get("customer_id")
    session_id = payload.get("session_id", str(uuid.uuid4()))

    try:
        # Short-term (session) memory: auto-provisioned by `agentcore deploy` into
        # BEDROCK_AGENTCORE_MEMORY_ID. Restores/persists raw conversation turns for
        # this session_id, separate from the long-term semantic memory below.
        stm_memory_id = os.environ.get("BEDROCK_AGENTCORE_MEMORY_ID")
        session_manager = None
        if stm_memory_id:
            session_manager = AgentCoreMemorySessionManager(
                AgentCoreMemoryConfig(
                    memory_id=stm_memory_id,
                    session_id=session_id,
                    actor_id=actor_id,
                ),
                region_name=REGION,
            )

        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID
        )
        agent_core_browser = AgentCoreBrowser(
            region=REGION,
            identifier="CustomerAgentBrowser-hTqSJMnv1u",
            session_timeout=600
        )

        client = MCPClient(
            lambda: streamable_http_client(url=GATEWAY_URL)
        )

        with client:
            tools = [search_knowledge_base, calculate_loyalty_discount, agent_core_browser.browser]
            tools.extend(client.list_tools_sync())
            agent = Agent(
                model=model,
                system_prompt=SYSTEM_PROMPT,
                tools=tools,
                state={"session_id": session_id, "actor_id": actor_id},
                hooks=[memory_hook],
                session_manager=session_manager,
            )

            response = agent(user_input)
            return response.message["content"][0]["text"]

    except Exception as e:
        logger.error("Agent invocation failed for actor %s: %s", actor_id, e)
        return "I'm sorry, something went wrong while processing your request. Please try again in a moment."





# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
