from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from agent import Deps, create_agent
from garmin_client import GarminClient


async def main() -> None:
    load_dotenv()

    garmin_email = os.getenv("GARMIN_EMAIL")
    garmin_password = os.getenv("GARMIN_PASSWORD")
    ai_base_url = os.getenv("AI_FOUNDRY_BASE_URL")
    ai_api_key = os.getenv("AI_FOUNDRY_API_KEY")

    if not all([garmin_email, garmin_password, ai_base_url, ai_api_key]):
        print("Missing required environment variables. See .env.example")
        sys.exit(1)

    # Login to Garmin
    print("Logging in to Garmin Connect...")
    garmin = GarminClient(garmin_email, garmin_password)
    try:
        garmin.login()
    except Exception as e:
        print(f"Failed to login to Garmin Connect: {e}")
        sys.exit(1)
    print("Logged in successfully.\n")

    # Create agent
    agent = create_agent(ai_base_url, ai_api_key)
    deps = Deps(garmin=garmin)

    print("Garmin Run Analyzer")
    print("=" * 40)
    print("Ask me anything about your running data.")
    print("Examples:")
    print("  - Show my last 5 runs")
    print("  - How has my pace changed over the last month?")
    print("  - Analyze my heart rate zones from recent runs")
    print("  - Compare my last two long runs")
    print("  - What workout should I do next?")
    print("  - Give me a weekly training plan")
    print("\nType 'quit' or 'exit' to stop.\n")

    message_history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        try:
            result = await agent.run(
                user_input,
                deps=deps,
                message_history=message_history,
            )
            message_history = result.all_messages()
            print(f"\nCoach: {result.data}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
