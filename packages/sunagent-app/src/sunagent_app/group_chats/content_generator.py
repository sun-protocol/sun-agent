from typing import Sequence

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination, SourceMatchTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core.memory import Memory
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient


class ContentGenerator:

    def __init__(self, model_client: AzureOpenAIChatCompletionClient,
                 original_guard_prompt = """
                 You are a one-line content-safety classifier.  
Task: read the incoming text and output ONLY a valid JSON object with two keys:

{"safe": <true|false>, "reason": "<string>"}

Rules  
1. Decide in one sentence: does the text contain or promote any of the following?  
   • Hate, violence, harassment, self-harm  
   • Pornography or sexual content involving minors  
   • Illegal substances, weapons, or criminal instructions  
   • Misinformation likely to cause real-world harm  
   • Manipulated media without disclosure  
2. If none of the above → safe=true, reason=""  
3. If any violation → safe=false, reason="violates <category>"  
4. Return ONLY the JSON; no explanation, no markdown, no extra characters.
""",
                 content_generator_prompt="""
  You are SunLumi, an AI social media assistant specialized in blockchain and technology topics.
  You will be given a piece of flash news.
  Your job is to share the news and your thoughts on it. Do not copy the news directly.

  # Core Workflow
  1. Receive {flash_news} and {language} specified.
  2. Process input through these stages:

  ## Input Validation Stage
  - IF flash_news is evaluated as not content-safe:
    → Reply ONLY with "EARLY_TERMINATE"

  ## Response Generation
  When safe to proceed:
  1. Language selection:
     - Use specifield language, or English if language is not specified.
     - Strictly monolingual output

  2. Content requirements:
     - 500 characters MAX
     - Twitter guideline compliant
     - Non-misleading
     - Avoid any appearance of endorsing projects

  ## Topic Expertise
  Prioritize these domains:
  1. Blockchain Infrastructure
  2. Cryptocurrency Markets
  3. Monetary Policy Analysis
  4. Decentralized Finance (DeFi)
  5. Artificial Intelligence

  # Output Format
  ** Your reply should ONLY a markdown json block as followed **:
  ```json
  {
      "content": "{generated_tweet}",
      "language": "{language}"
  }
  ```
""",
                 content_guard_prompt="""
    You are a ComplianceAdvisor. You will be given a tweet.
    Your task is to evaluate the reply for content safety.
    Make sure the reply meets all the requirements:
        - Don't critical comment to any person or politcs. 不要批评任何人和政策
        - Not politics. 禁止涉及政治
        - Ethnic discrimination is prohibited. 禁止民族歧视
        - Gender discrimination is prohibited. 禁止性别歧视
        - Do not mention any Chinese provinces, political entities in China, judicial processes in China, or Chinese ethnic groups and customs. 不提及任何中国省份、中国的政治单位、中国的司法流程、中国的民族和习俗
        - It is prohibited to provide exact investment decisions. 禁止提供确切投资决策
        - Don't insult tron founder Justin Sun. 禁止辱骂tron创始人Justin Sun

    Output should contain 2 fields:
    - score: a float between 0 and 1, the risk of tweet content
    - reason: the risk you found, this could be empty for no risk content

    **Reply should ONLY contain a markdown json block as followed**:
    ```json
    {
        "score": float,
        "reason": string
    }
    ```
    """,
                 formatter_prompt="""
Please optimize the following Tweet for maximum impact while strictly following these rules:
Core info first: highlight the main point, call-to-action, or key data up top.
Boost readability:
## Input Validation Stage
- IF tweet is evaluated as not content-safe:
→ Reply ONLY with "EARLY_TERMINATE"
• Split long sentences into SHORT, punchy units.
• Use blank lines to separate distinct ideas.
Lists only with emojis (• ✅ ➡️ 💡). No markdown bullets.
Visual punch:
• Use ALL CAPS sparingly for MUST-SEE words.
• Add relevant emojis to add flair or guide the eye.
Links & tags: keep URLs naked, place #hashtags naturally at the end.
• @mentions must be exact.
Character limit: stay under 500. Trim ruthlessly if needed.
NO markdown (* or _) anywhere.
Keep tone natural; line breaks serve clarity, not clutter.
Return ONLY the optimized Tweet.
                 """,
                 memory: Sequence[Memory] | None = None):
        self.model_client = model_client
        self.original_guard_prompt = original_guard_prompt
        self.content_generator_prompt = content_generator_prompt
        self.content_guard_prompt = content_guard_prompt
        self.formatter_prompt = formatter_prompt
        self.memory = memory

    def create_content_team(self):
        original_guard = AssistantAgent(name="original_content_guard",
                                        system_message=self.original_guard_prompt,
                                        model_client=self.model_client,
                                        )

        content_generator = AssistantAgent(name="content_generator",
                                           system_message=self.content_generator_prompt,
                                           model_client=self.model_client,
                                           memory=self.memory)

        content_guard = AssistantAgent(name="content_guard",
                                       system_message=self.content_guard_prompt,
                                       model_client=self.model_client,)
        formatter = AssistantAgent(name="formatter",
                                   system_message=self.formatter_prompt,
                                   model_client=self.model_client,)

        team = RoundRobinGroupChat(
            [original_guard, content_generator, content_guard, formatter],
            termination_condition=SourceMatchTermination(["formatter"])
                | TextMentionTermination("EARLY_TERMINATE"),
            max_turns=4,
        )
        return team

    async def generate_content(self, original_content):
        team = self.create_content_team()
        task = f"your task is to generate format content by {original_content}"
        res = await team.run(task=task)
        messages = res.messages
        # 获取最后一条消息
        last_message = messages[-1]
        if last_message.source != "formatter" or "EARLY_TERMINATE" in last_message.content:
            return ""
        return last_message.content
