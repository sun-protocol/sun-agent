# -*- coding: utf-8 -*-
import asyncio
import hashlib
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import (
    Dict,
    List,
    Optional,
)

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from autogen_agentchat.agents import AssistantAgent, SocietyOfMindAgent
from autogen_agentchat.base import Response as TaskResponse
from autogen_agentchat.base import TaskResult
from autogen_agentchat.conditions import (
    SourceMatchTermination,
    TextMentionTermination,
)
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.teams import MagenticOneGroupChat, RoundRobinGroupChat
from autogen_agentchat.ui import Console
from autogen_ext.agents.web_surfer import MultimodalWebSurfer
from autogen_ext.cache_store.redis import RedisStore
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from playwright.async_api import BrowserContext, Playwright, async_playwright
from quart import Quart, Response, jsonify, request
from redis import Redis
from tweepy import Client as TwitterClient

from sunagent_app._constants import LOGGER_NAME
from sunagent_app.agents import (
    ContextBuilderAgent,
    TweetAnalysisAgent,
    TweetCheckAgent,
)
from sunagent_app.agents._markdown_utils import extract_markdown_json_blocks
from sunagent_app.memory import get_knowledge_memory, get_profile_memory
from sunagent_app.templates.twitter_templates import (
    BlockPatterns,
    CommonJobRequirment,
    FlashTweetCheckTemplate,
    PostFlashTweetPrompt,
    TweetCheckTemplate,
    TweetPostKnowledge,
    TweetReplyTemplate,
    TweetsAnalysisTemplate,
)
from dotenv import load_dotenv
# 加载 .env 文件
load_dotenv()
logging_config = os.getenv("LOGGING_CONFIG", "logging_config.yaml")
with open(logging_config, "r") as f:
    config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)

logger = logging.getLogger(LOGGER_NAME)
UTC8 = timezone(timedelta(hours=8))


class SunAgentSystem:
    def __init__(self) -> None:
        self.agent_id = os.getenv("AGENT_ID")
        self._initialized = False

    async def setup(self):
        if self._initialized:
            return
        self.twitter_client = TwitterClient(
            bearer_token=os.getenv("TW_BEARER_TOKEN"),
            consumer_key=os.getenv("TW_CONSUMER_KEY"),
            consumer_secret=os.getenv("TW_CONSUMER_SECRET"),
            access_token=os.getenv("TW_ACCESS_TOKEN"),
            access_token_secret=os.getenv("TW_ACCESS_TOKEN_SECRET"),
        )
        self.user_auth = self.twitter_client.access_token_secret is not None
        self.cache = None
        if os.getenv("REDIS_URL"):
            expire: Optional[int] = int(os.getenv("REDIS_EXPIRE")) if os.getenv("REDIS_EXPIRE") else None
            self.cache = RedisStore[str](
                Redis.from_url(os.getenv("REDIS_URL"), socket_connect_timeout=10, socket_timeout=10), expire=expire
            )
        self.context_builder = ContextBuilderAgent(
            self.agent_id,
            twitter_client=self.twitter_client,
            cache=self.cache,
            timeout=int(os.getenv("TW_TIMEOUT", "60")),
        )
        self.openai = AzureOpenAIChatCompletionClient(
            model=os.getenv("OPENAI_MODEL"),
            azure_deployment=os.getenv("OPENAI_DEPLOYMENT"),
            api_version=os.getenv("OPENAI_API_VERSION"),
            azure_endpoint=os.getenv("OPENAI_ENDPOINT"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0,
        )
        self.content_model = AzureOpenAIChatCompletionClient(
            model=os.getenv("OPENAI_MODEL"),
            azure_deployment=os.getenv("OPENAI_DEPLOYMENT"),
            api_version=os.getenv("OPENAI_API_VERSION"),
            azure_endpoint=os.getenv("OPENAI_ENDPOINT"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        self.playwright, self.user_context = await self._create_browser()
        self.home_timeline_team = await self.create_home_timeline_team()
        self.mentions_timeline_team = await self.create_mentions_timeline_team()
        self.crawler_team = await self.create_crawler_team()
        self.post_flash_team = await self.create_post_flash_team()
        self.tweet_post_team = await self.create_tweet_post_team()
        self.tweet_post_semaphore = asyncio.Semaphore(1)
        self._initialized = True

    async def create_home_timeline_team(self):
        analysis_agent = TweetAnalysisAgent(
            name="TweetsAnalysisAgent",
            description=TweetsAnalysisTemplate["description"],
            system_message=TweetsAnalysisTemplate["prompt"],
            model_client=self.openai,
            batch_size=10,
        )
        content_generator = AssistantAgent(
            name="ContentGenerator",
            description=TweetReplyTemplate["description"],
            system_message=TweetReplyTemplate["prompt"],
            model_client=self.content_model,
            memory=get_profile_memory() + get_knowledge_memory(),
            reflect_on_tool_use=True,
        )
        comliance_advisor = TweetCheckAgent(
            name="ComplianceAdvisor",
            description=TweetCheckTemplate["description"],
            system_message=TweetCheckTemplate["prompt"],
            model_client=self.openai,
            block_patterns=BlockPatterns,
            skip_task_description=True,
        )
        reply_agent = SocietyOfMindAgent(
            name="ReplyAgent",
            team=RoundRobinGroupChat(
                [content_generator, comliance_advisor],
                termination_condition=TextMentionTermination("TERMINATE"),
                max_turns=2,
            ),
            model_client=self.openai,
            description="""
            An agent that replys user's tweet.
            The reply is a markdown json block.
            """,
            response_prompt="Extract(without modify) the markdown json reply that is generated by ContentGenerator if its evaluate result is safe, otherwise say 'EARLY_TERMINATE'",
        )

        return RoundRobinGroupChat(
            [analysis_agent, comliance_advisor, reply_agent],
            termination_condition=SourceMatchTermination(["ReplyAgent"]) | TextMentionTermination("TERMINATE"),
        )

    async def create_mentions_timeline_team(self):
        comliance_advisor = TweetCheckAgent(
            name="ComplianceAdvisor",
            description=TweetCheckTemplate["description"],
            system_message=TweetCheckTemplate["prompt"],
            model_client=self.openai,
            block_patterns=BlockPatterns,
        )
        content_generator = AssistantAgent(
            name="ContentGenerator",
            description=TweetReplyTemplate["description"],
            system_message=TweetReplyTemplate["prompt"],
            model_client=self.content_model,
            memory=get_profile_memory() + get_knowledge_memory(),
        )
        reply_agent = SocietyOfMindAgent(
            name="ReplyAgent",
            team=RoundRobinGroupChat(
                [content_generator, comliance_advisor],
                termination_condition=TextMentionTermination("TERMINATE"),
                max_turns=2,
            ),
            model_client=self.openai,
            description="""
            An agent that replys user's tweet.
            The reply is a markdown json block.
            """,
            response_prompt="Extract(without modify) the markdown json reply that is generated by ContentGenerator if its evaluate result is safe, otherwise say 'EARLY_TERMINATE'",
        )
        return RoundRobinGroupChat(
            [comliance_advisor, reply_agent],
            termination_condition=SourceMatchTermination(["ReplyAgent"]) | TextMentionTermination("TERMINATE"),
        )

    async def create_crawler_team(self):
        # crawler do not need user context, launch a different browser
        browser = await self.playwright.chromium.launch(
            executable_path=os.getenv("BROWSER", "/usr/bin/google-chrome-stable"),
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
        )
        web_surfer = MultimodalWebSurfer(
            "WebSurfer",
            model_client=self.openai,
            headless=True,
            playwright=self.playwright,
            context=context,
        )
        assistant = AssistantAgent(
            name="Assistant",
            description="An agent helping to filter flashes by given rules",
            model_client=self.openai,
        )
        return MagenticOneGroupChat(
            [web_surfer, assistant],
            model_client=self.openai,
            termination_condition=TextMentionTermination("TERMINATE") | SourceMatchTermination("Assistant"),
            max_turns=10,
        )

    async def create_post_flash_team(self):
        content_generator = AssistantAgent(
            name="ContentGenerator",
            description=PostFlashTweetPrompt["description"],
            system_message=PostFlashTweetPrompt["prompt"],
            model_client=self.content_model,
            memory=get_profile_memory() + get_knowledge_memory(),
        )
        comliance_advisor = TweetCheckAgent(
            name="ComplianceAdvisor",
            description=FlashTweetCheckTemplate["description"],
            system_message=FlashTweetCheckTemplate["prompt"],
            model_client=self.openai,
            block_patterns=BlockPatterns,
            skip_task_description=True,
        )
        post_agent = SocietyOfMindAgent(
            name="PostAgent",
            team=RoundRobinGroupChat(
                [content_generator, comliance_advisor],
                termination_condition=SourceMatchTermination(["ComplianceAdvisor"])
                | TextMentionTermination("TERMINATE"),
            ),
            model_client=self.openai,
            description=PostFlashTweetPrompt["description"],
            response_prompt="Extract(without modify) the markdown json result that is generated by ContentGenerator if its evaluate result is safe, otherwise say 'EARLY_TERMINATE'",
        )
        return RoundRobinGroupChat([post_agent], max_turns=1)

    async def create_tweet_post_team(self):
        web_surfer = MultimodalWebSurfer(
            "WebSurfer",
            model_client=self.openai,
            playwright=self.playwright,
            context=self.user_context,
            start_page="https://x.com",
        )
        return MagenticOneGroupChat(
            [web_surfer],
            model_client=self.openai,
            termination_condition=TextMentionTermination("TERMINATE"),
            max_turns=10,
        )

    async def _create_browser(self) -> (Playwright, BrowserContext):
        playwright = await async_playwright().start()
        context = await playwright.chromium.launch_persistent_context(
            executable_path=os.getenv("BROWSER", "/usr/bin/google-chrome-stable"),
            user_data_dir=os.getenv("BROWSER_DATA_DIR", "/root/.config/google-chrome"),
            headless=False,
            slow_mo=1000,
            args=[
                "--no-sandbox",
                "--profile-directory=Default",
                # "--disable-blink-features=AutomationControlled",
                # "--disable-infobars",
                # "--disable-background-timer-throttling",
                # "--disable-popup-blocking",
                # "--disable-backgrounding-occluded-windows",
                # "--disable-renderer-backgrounding",
                # "--disable-window-activation",
                # "--disable-focus-on-load",
                # "--no-first-run",
                # "--no-default-browser-check",
                # "--no-startup-window",
                # "--window-position=0,0",
                # "--disable-web-security",
                # "--disable-site-isolation-trials",
                # "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        return playwright, context

    async def home_timeline_task(self) -> None:
        logger.info("running home timeline task")
        tweets = await self.context_builder.get_home_timeline_with_context()
        logger.info(f"home timeline tweets: {tweets}")

        if tweets != "[]":
            task = f"""
            ## Job description:
            Choose one tweet from given tweets, then evaluate it whether is content safe.
            Reply to the tweet according to the content and make sure the reply is meaningful and evaluated as content safe for publishing.

            {CommonJobRequirment.format(steps=10)}

            ```json
            {tweets}
            ```
            """
            result = await Console(self.home_timeline_team.run_stream(task=task))
            await self.home_timeline_team.reset()
            await self.post_tweet(result)

    async def post_tweet(self, result):
        if isinstance(result, TaskResult):
            message = result.messages[-1]
        elif isinstance(result, TaskResponse):
            message = result.chat_message
        assert isinstance(message, TextMessage)
        blocks = extract_markdown_json_blocks(message.content)
        for block in blocks:
            if not isinstance(block, Dict):
                continue
            task = f"""{TweetPostKnowledge}

            {CommonJobRequirment.format(steps=5)}

            post this tweet:
            ```json
            {json.dumps(block, ensure_ascii=False)}
            ```
            """
            async with self.tweet_post_semaphore:
                await Console(self.tweet_post_team.run_stream(task=task))
                await self.tweet_post_team.reset()

    async def mentions_timeline_task(self) -> None:
        logger.info("running mentions timeline task")
        mentions = json.loads(await self.context_builder.get_mentions_with_context())

        assert isinstance(mentions, List)
        for mention in mentions:
            task = f"""
            ## Job description:
            Evaluate the given tweet whether it's content safe and then reply to it according to the content.
            Make sure the reply is meaningful and evaluated as content safe for publishing.

            {CommonJobRequirment.format(steps=10)}

            ```json
            {json.dumps(mention, ensure_ascii=False)}
            ```
            """
            result = await Console(self.mentions_timeline_team.run_stream(task=task))
            await self.mentions_timeline_team.reset()
            await self.post_tweet(result)
            await asyncio.sleep(60)

    async def news_flash_task(self):
        logger.info("running news flash task")
        urls = json.loads(os.getenv("NEWS_URLS", "[]"))
        flashes = []
        for url in urls:
            task = f"""
            ## Job description
            You need to go to '{url}' and extract the flashes that posted in the past 20 minutes.
            Ensure every flash has 'title', 'content', and 'time', remember *DO NOT* modify flash content(including language).
            Filter and output the flashes that meets the following requirements strictly from the flashes extracted before:
            1. The flash content focuses on these fields:
                - blockchain
                - cryptocurrencies
                - monetary policy
                - AI
                - DEFI
                - public chains
            2. The flash content does not contain information related to these topics:
                - coin price fluctuations
                - exchange listing announcements
                - whale operations
                - other types of information inclined towards cryptocurrency speculation
                - new coin promotions (including but not limited to presales, whitelist events, TGE announcements)
                - promotional campaigns for new tokens or projects
            3. Exclude flashes with few information content or negative social impact
            4. Additional filtering criteria for new coin promotions:
                - Filter out any content mentioning token launches or initial offerings
                - Exclude announcements about fundraising rounds (IDO/IEO/ICO)
                - Remove content promoting exclusive access or early participation opportunities

            {CommonJobRequirment.format(steps=5)}
            - Once you get any flashes after filtering, return immediate

            ## Output format
            Return a json array of flashes(empty json array is allowed):
            ```json
            [
                {{
                    'title': '{{title}}',
                    'content': '{{content}}',
                    'time': '{{time}}'
                }}
            ]
            ```
            """
            result = await Console(self.crawler_team.run_stream(task=task))
            await self.crawler_team.reset()
            if isinstance(result, TaskResult):
                message = result.messages[-1]
            elif isinstance(result, TaskResponse):
                message = result.chat_message
            assert isinstance(message, TextMessage)
            blocks = extract_markdown_json_blocks(message.content)
            assert self.cache
            if len(blocks) > 0 and isinstance(blocks[0], List):
                flashes = blocks[0]
                for flash in flashes:
                    if not isinstance(flash, Dict) or "title" not in flash or "content" not in flash:
                        continue
                    cache_key = f"{self.agent_id}:F:{hashlib.md5(flash["title"].strip().encode("utf-8")).hexdigest()}"
                    logger.info(f"Processing flash {flash} key: {cache_key}")
                    if self.cache.get(cache_key) is not None:
                        logger.warning(f"key: {cache_key} has been processed before")
                        continue
                    languages = ["English", "Chinese"]
                    language = random.choice(languages)
                    task = f"""
                    ## Job description:
                    Generate and post a tweet to share the given news flash and your thought on it.
                    Make sure the post content is evaluated as content safe for publishing.

                    {CommonJobRequirment.format(steps=10)}
                    - Use {language} to generate your tweet

                    ```json
                    {json.dumps(flash, ensure_ascii=False)}
                    ```
                    """
                    await asyncio.sleep(60)
                    result = await Console(self.post_flash_team.run_stream(task=task))
                    await self.post_flash_team.reset()
                    self.cache.set(cache_key, "")
                    await self.post_tweet(result)
                    break


agent = SunAgentSystem()
scheduler = AsyncIOScheduler()
app = Quart(agent.agent_id)


@app.route("/tweet", methods=["POST"])
async def post_twitter() -> Response:
    data = await request.get_json()
    logger.info("/tweet post data=%s", data)
    code, text = await agent.context_builder.create_tweet(data)
    return jsonify({"error_code": code if code != 0 else 200, "text": text})


async def main() -> None:
    logger.info("SunAgent start")
    await agent.setup()
    hour = os.getenv("FLASH_TASK_HOUR", "10-20")
    minutes = os.getenv("FLASH_TASK_MINUTES", "*/20")
    scheduler.add_job(
        agent.news_flash_task,
        trigger="cron",
        hour=hour,
        minute=minutes,
        timezone=UTC8,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
    )
    minutes = int(os.getenv("HOME_TASK_INTERVAL_MINUTES", "60"))
    scheduler.add_job(
        agent.home_timeline_task,
        trigger="interval",
        minutes=minutes,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
    )
    minutes = int(os.getenv("MENTIONS_TASK_INTERVAL_MINUTES", "2"))
    scheduler.add_job(
        agent.mentions_timeline_task,
        trigger="interval",
        minutes=minutes,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
    )
    scheduler.start()

    # try:
    #     await asyncio.Future()
    # except Exception:
    #     logger.info("SunAgent stop")
    # start web app
    port = int(os.getenv("HTTP_PORT", "9529"))
    await app.run_task(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    asyncio.run(main())
