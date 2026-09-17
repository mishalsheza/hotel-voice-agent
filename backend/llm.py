import json
from openai import OpenAI
from backend.config import config
from backend.tools import TOOLS, SYSTEM_PROMPT

class LLMService:
    def __init__(self):
        self.api_key = config.GROQ_API_KEY
        self.use_fallback = not self.api_key

        if self.use_fallback:
            print("⚠️ No Groq API key - using fallback keyword matching")
        else:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            print(f"✅ Groq client initialized")

    async def process_intent(self, transcript: str, history: list = None) -> dict:
        if self.use_fallback:
            return self._fallback_processing(transcript)

        try:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": transcript})

            response = self.client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                timeout=30.0,
            )

            msg = response.choices[0].message

            result = {
                "tool_calls": [],
                "response": None,
                "end_call": False,
                "farewell": None,
            }

            if msg.tool_calls:
                for tool_call in msg.tool_calls:
                    name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments or "{}")

                    if name == "end_call":
                        result["end_call"] = True
                        result["farewell"] = args.get("farewell")
                    else:
                        result["tool_calls"].append({
                            "name": name,
                            "arguments": args,
                            "id": tool_call.id,
                        })
            else:
                result["response"] = msg.content or "Could you tell me more?"

            return result

        except Exception as e:
            print(f"❌ LLM error: {e}")
            return self._fallback_processing(transcript)

    def _fallback_processing(self, transcript: str) -> dict:
        """Fallback keyword-based processing"""
        import re
        normalized = re.sub(r"[^\w\s]", " ", transcript.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()

        result = {"tool_calls": [], "response": None, "end_call": False, "farewell": None}

        end_phrases = [
            "no thanks", "no thank you", "thats all", "that is all",
            "thats it", "that is it", "nothing else", "im good", "i am good",
            "im all set", "all set", "bye", "goodbye", "good bye",
        ]
        if any(re.search(rf"\b{re.escape(p)}\b", normalized) for p in end_phrases):
            result["end_call"] = True
            result["farewell"] = "Thank you for calling. Have a great stay!"
            return result

        lower = transcript.lower()
        if "towel" in lower:
            result["tool_calls"].append({
                "name": "create_ticket",
                "arguments": {
                    "room": "402", "request_type": "housekeeping", "item": "towels",
                    "quantity": 2, "priority": "medium", "description": transcript,
                },
            })
        elif "wifi" in lower or "password" in lower:
            result["response"] = "The Wi-Fi password is 'HotelGuest2024'"
        elif "checkout" in lower:
            result["response"] = "Checkout is at 11 AM. Late checkout available until 2 PM."
        elif "ac" in lower or "air conditioning" in lower:
            result["tool_calls"].append({
                "name": "create_ticket",
                "arguments": {
                    "room": "402", "request_type": "maintenance", "item": "air conditioning",
                    "quantity": 1, "priority": "high", "description": transcript,
                },
            })
        else:
            result["response"] = "I'm not sure I understood. You can ask about towels, Wi-Fi, checkout, or AC."

        return result

llm = LLMService()