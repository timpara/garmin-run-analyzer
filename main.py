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
    print("(If MFA is required, check your email for a verification code)")
    garmin = GarminClient(garmin_email, garmin_password)
    try:
        garmin.login()
    except Exception as e:
        err = str(e)
        if "429" in err or "rate" in err.lower():
            print(f"\nRate limited by Garmin. Wait a few minutes and try again.")
        else:
            print(f"\nFailed to login to Garmin Connect: {e}")
        sys.exit(1)
    print("Logged in successfully.\n")

    # Create agent
    agent = create_agent(ai_base_url, ai_api_key)
    deps = Deps(garmin=garmin)

    print("Garmin Run Analyzer")
    print("=" * 40)
    print("Ask me anything about your running data.")
    print("Examples:")
    print("  - Am I recovered enough to do a hard workout today?")
    print("  - Assess my current fitness and training load")
    print("  - Is my easy/hard intensity distribution right?")
    print("  - How has my pace changed over the last month?")
    print("  - What workout should I do next, and why?")
    print("  - Build me a training week toward a sub-20 5k")
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
            print(f"\nCoach: {result.output}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
