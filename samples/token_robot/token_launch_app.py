# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import os
import random
import traceback
from datetime import datetime, timedelta, timezone
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Sequence,
)

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Response as TaskResponse
from autogen_agentchat.base import TaskResult
from autogen_agentchat.conditions import (
    SourceMatchTermination,
    TextMentionTermination,
)
from autogen_agentchat.messages import (
    TextMessage,
)
from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat
from autogen_agentchat.ui import Console
from autogen_core.models import ModelFamily, UserMessage
from autogen_ext.cache_store.redis import RedisStore
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from autogen_ext.tools.mcp import SseServerParams, mcp_server_tools
from dotenv import load_dotenv
from quart import Quart, Response, jsonify, request
from redis import Redis
from sunagent_app._constants import LOGGER_NAME
from sunagent_app.agents import (
    ContextBuilderAgent,
    ImageGenerateAgent,
    MentionStream,
    TokenLaunchAgent,
    TweetAnalysisAgent,
    TweetCheckAgent,
)
from sunagent_app.memory import get_knowledge_memory, get_sungenx_profile_memory
from sunagent_app.sunpump_service import SunPumpService
from sunagent_app.templates.token_templates import (
    IntentRecognition,
    PromoteTemplates,
    ShowCaseTemplates,
    TokenImageGeneration,
    TokenInfoExtraction,
    TokenInfoGeneration,
    TokenLaunchAssistant,
    TokenLaunchReply,
    TweetCheckReplyTemplate,
    TweetReplyTemplate,
)
from sunagent_app.templates.twitter_templates import (
    BlockPatterns,
    TweetCheckTemplate,
)
from tweepy import Client as TwitterClient
from tweepy import StreamResponse, TweepyException

# 加载 .env 文件
load_dotenv()

logging_config = os.getenv("LOGGING_CONFIG", "logging_config.yaml")
with open(logging_config, "r") as f:
    config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)

logger = logging.getLogger(LOGGER_NAME)
UTC8 = timezone(timedelta(hours=8))

languages = ["english" * 9, "chinese"]


async def create_tools():
    url = os.environ.get("PUMP_MCP_URL", "")
    if not url:
        return []
        # raise ValueError("PUMP_MCP_URL is not set")
    server_params = SseServerParams(
        url=url,
        headers={},
        timeout=30,  # Connection timeout in seconds
    )
    return await mcp_server_tools(server_params)


class SunAgentSystem:
    def __init__(self) -> None:
        self.agent_id = os.getenv("AGENT_ID")
        self.twitter_client = TwitterClient(
            consumer_key=os.getenv("TW_CONSUMER_KEY"),
            consumer_secret=os.getenv("TW_CONSUMER_SECRET"),
            access_token=os.getenv("TW_ACCESS_TOKEN"),
            access_token_secret=os.getenv("TW_ACCESS_TOKEN_SECRET"),
        )
        self.cache = None
        if os.getenv("REDIS_URL"):
            expire: Optional[int] = int(os.getenv("REDIS_EXPIRE")) if os.getenv("REDIS_EXPIRE") else None
            self.cache = RedisStore[str](
                Redis.from_url(os.getenv("REDIS_URL"), socket_connect_timeout=10, socket_timeout=10), expire=expire
            )
        self.sunpump_ops_service = SunPumpService(host=os.getenv("SUNPUMP_OPS_HOST"))
        self.sunpump_tools = create_tools()
        self.context_builder = ContextBuilderAgent(
            self.agent_id,
            twitter_client=self.twitter_client,
            cache=self.cache,
            timeout=int(os.getenv("TW_TIMEOUT", "60")),
        )
        self.mention_stream = MentionStream(
            self.on_twitter_response,
            bearer_token=os.getenv("TW_BEARER_TOKEN"),
        )
        self.tools_model = AzureOpenAIChatCompletionClient(
            model=os.getenv("OPENAI_MODEL"),
            azure_deployment=os.getenv("OPENAI_DEPLOYMENT"),
            api_version=os.getenv("OPENAI_API_VERSION"),
            azure_endpoint=os.getenv("OPENAI_ENDPOINT"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0,
        )
        self.text_model = AzureOpenAIChatCompletionClient(
            model=os.getenv("OPENAI_MODEL"),
            azure_deployment=os.getenv("OPENAI_DEPLOYMENT"),
            api_version=os.getenv("OPENAI_API_VERSION"),
            azure_endpoint=os.getenv("OPENAI_ENDPOINT"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    def _create_tweet_team(self):
        tweet_generator = AssistantAgent(
            name="TweetGenerator",
            system_message="Your task is to generate a tweet, **ONLY** return the tweet content",
            model_client=self.text_model,
            memory=get_sungenx_profile_memory(),
        )
        return RoundRobinGroupChat(
            [tweet_generator],
            max_turns=1,
        )

    def _intent_recognition(self):
        intent_recognition = AssistantAgent(
            name="IntentRecognition",
            description=IntentRecognition["description"],
            system_message=IntentRecognition["prompt"],
            model_client=self.tools_model,
        )
        return RoundRobinGroupChat([intent_recognition], max_turns=1)

    def _interaction(self):
        compliance_advisor1 = TweetCheckAgent(
            name="ComplianceAdvisor1",
            description=TweetCheckTemplate["description"],
            system_message=TweetCheckReplyTemplate["prompt"],
            model_client=self.text_model,
            block_patterns=BlockPatterns,
        )
        reply_agent = AssistantAgent(
            name="ReplyAgent",
            description=TweetReplyTemplate["description"],
            system_message=TweetReplyTemplate["prompt"],
            model_client=self.text_model,
            tools=self.sunpump_tools,
            reflect_on_tool_use=True,
            reflection_system_message=TweetReplyTemplate["prompt"],
            memory=get_sungenx_profile_memory() + get_knowledge_memory(),
        )
        compliance_advisor2 = TweetCheckAgent(
            name="ComplianceAdvisor2",
            description=TweetCheckReplyTemplate["description"],
            system_message=TweetCheckReplyTemplate["prompt"],
            model_client=self.text_model,
            block_patterns=BlockPatterns,
        )
        publisher = AssistantAgent(
            name="Publisher",
            description="An Agent to post a generated tweet reply.",
            system_message="""Your task is to post the generated tweet reply checked by ComplianceAdvisor.
            """,
            tools=[self.context_builder.reply_tweet],
            model_client=self.tools_model,
        )
        termination = SourceMatchTermination(["Publisher"]) | TextMentionTermination("EARLY_TERMINATE")
        interaction_team = RoundRobinGroupChat(
            [compliance_advisor1, reply_agent, compliance_advisor2, publisher],
            termination_condition=termination,
        )
        return interaction_team

    def _launch_token(self):
        information_extractor = AssistantAgent(
            name="InformationExtractor",
            description=TokenInfoExtraction["description"],
            system_message=TokenInfoExtraction["prompt"],
            model_client=self.tools_model,
        )
        token_launcher = TokenLaunchAgent(
            name="TokenLauncher",
            model_client=self.tools_model,
            system_message=TokenInfoGeneration["prompt"],
            image_model="imagen-3.0-generate-002",
            image_prompts=TokenImageGeneration["prompt"],
            sunpump_service=self.sunpump_ops_service,
        )
        reply_agent = AssistantAgent(
            name="ReplyAgent",
            model_client=self.text_model,
            description=TokenLaunchReply["description"],
            system_message=TokenLaunchReply["prompt"],
            memory=get_sungenx_profile_memory(),
        )
        publisher = AssistantAgent(
            name="Publisher",
            description="An Agent to post a generated tweet reply.",
            system_message="""Your task is to post the generated tweet reply checked by ComplianceAdvisor.
            """,
            tools=[self.context_builder.reply_tweet],
            model_client=self.tools_model,
        )
        termination = SourceMatchTermination(["Publisher"]) | TextMentionTermination("EARLY_TERMINATE")
        token_launch_team = RoundRobinGroupChat(
            [
                information_extractor,
                token_launcher,
                reply_agent,
                publisher,
            ],
            termination_condition=termination,
        )
        return token_launch_team

    # def _token_launch_selector(self, thread: Sequence[AgentEvent | ChatMessage]) -> str | None:
    #     if len(thread) == 0:
    #         return None
    #     idx: int = len(thread) - 1
    #     while idx >= 0:
    #         if not isinstance(thread[idx], BaseAgentEvent):
    #             break
    #         idx -= 1
    #     if idx < 0:
    #         return None
    #     last_message = thread[idx]
    #     if last_message.source == self.publisher.name:
    #         return None
    #     elif last_message.source == self.comliance_advisor2.name:
    #         return self.publisher.name
    #     elif last_message.source == self.assistant.name:
    #         if isinstance(last_message, TextMessage):
    #             return self.comliance_advisor2.name
    #         else:
    #             return self.assistant.name
    #     elif last_message.source == self.image_generator.name:
    #         return self.assistant.name
    #     elif last_message.source == self.information_extractor.name:
    #         return self.image_generator.name
    #     else:
    #         return self.information_extractor.name

    async def daily_report(self) -> None:
        logger.info("running daily report task")
        now = datetime.now(UTC8)
        today = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=UTC8)
        from_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        data = await self.sunpump_api_service.query_transaction_summary_by_date(from_date, to_date)
        if data.startswith("[") and data != "[]":
            try:
                task = f"""
                Post a new tweet about how SunGenX is doing base on given data.

                description to data fields:
                - date: date string in "YYYY-mm-dd"
                - tokenCreated: numbers of token created in SunPump
                - tokenLaunched: numbers of token launched in SunSwap
                - txVirtual: numbers of transactions in SunPump
                - txSwap: numbers of transactions in SunSwap(OTC)
                - volumeUsdVirtual: transaction amount in USD in SunPump
                - volumeUsdSwap: transaction amount in USD in SunSwap(OTC)
                - volumeTrxVirtual: transaction amount in TRX in SunPump
                - volumeTrxSwap: transaction amount in TRX in SunSwap(OTC)

                ```json
                {data}
                ```
                """
                result = await Console(self._create_tweet_team().run_stream(task=task))
                if isinstance(result, TaskResult):
                    message = result.messages[-1]
                elif isinstance(result, TaskResponse):
                    message = result.chat_message
                assert isinstance(message, TextMessage)
                content = message.content
                code, msg = await self.context_builder.create_tweet({"text": content})
                if code == 0:
                    logger.info(f"promote_task: {content}")
                else:
                    logger.error(f"error promote_task: {msg}")
                await asyncio.sleep(random.randint(5, 60) * 60)
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error(f"error promote_task: {e}")

        data = await self.sunpump_api_service.query_surge_tokens()
        if data.startswith("[") and data != "[]":
            try:
                task = f"""
                Post a new tweet about SunPump base on given token data in the past 24 hours.

                description to data fields:
                - name: name of token
                - symbol: symbol of token
                - contractAddress: address of token
                - swapPoolAddress: SunSwap pool address of token/TRX
                - description: the description of token
                - priceInTrx: the price of token in TRX
                - volume24Hr: transaction amount over the past 24 hours
                - priceChange24Hr: rate of change in percentage over the past 24 hours

                ```json
                {data}
                ```
                """
                result = await Console(self._create_tweet_team().run_stream(task=task))
                if isinstance(result, TaskResult):
                    message = result.messages[-1]
                elif isinstance(result, TaskResponse):
                    message = result.chat_message
                assert isinstance(message, TextMessage)
                content = message.content
                code, msg = await self.context_builder.create_tweet({"text": content})
                if code == 0:
                    logger.info(f"promote_task: {content}")
                else:
                    logger.error(f"error promote_task: {msg}")
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error(f"error promote_task: {e}")

    async def on_twitter_response(self, response: StreamResponse, cache_key: str) -> None:
        mentions, _ = await self.context_builder.on_twitter_response(response, cache_key)
        for mention in mentions:
            result = await self.sunpump_ops_service.can_launch_new_token(mention["author"])
            mention["can_launch_new_token"] = result
            conversation = f"""
            ```json
            {json.dumps(mention, ensure_ascii=False)}
            ```
            """
            result = await Console(self._intent_recognition().run_stream(task=conversation))
            if isinstance(result, TaskResult):
                message = result.messages[-1]
            elif isinstance(result, TaskResponse):
                message = result.chat_message
            assert isinstance(message, TextMessage)
            if message.content.strip() == "LaunchToken":
                asyncio.create_task(Console(self._launch_token().run_stream(task=conversation)))
            else:
                asyncio.create_task(
                    Console(
                        self._interaction().run_stream(
                            task=f"reply to the last tweet in this conversation: {conversation}"
                        )
                    )
                )
            # do not submit task too quickly, because of model service limits and twitter API limits
            await asyncio.sleep(random.randint(10, 30))

    async def mentions_task(self) -> None:
        logger.info("running mentions timeline task")
        mentions = json.loads(await self.context_builder.get_mentions_with_context())

        assert isinstance(mentions, List)
        for mention in mentions:
            result = await self.sunpump_ops_service.can_launch_new_token(mention["author"])
            mention["can_launch_new_token"] = result
            conversation = f"""
            ```json
            {json.dumps(mention, ensure_ascii=False)}
            ```
            """
            result = await Console(self._intent_recognition().run_stream(task=conversation))
            if isinstance(result, TaskResult):
                message = result.messages[-1]
            elif isinstance(result, TaskResponse):
                message = result.chat_message
            assert isinstance(message, TextMessage)
            if message.content.strip() == "LaunchToken":
                asyncio.create_task(Console(self._launch_token().run_stream(task=conversation)))
            else:
                asyncio.create_task(
                    Console(
                        self._interaction().run_stream(
                            task=f"reply to the last tweet in this conversation: {conversation}"
                        )
                    )
                )
            # do not submit task too quickly, because of model service limits and twitter API limits
            await asyncio.sleep(random.randint(10, 30))

    async def promote_task(self):
        # random delay 0-7 hour
        await asyncio.sleep(random.randint(0, 7) * 3600)
        logger.info("running promote task")
        template = PromoteTemplates[random.randint(0, len(PromoteTemplates) - 1)]
        try:
            language = random.choice(languages)
            task = f"""
            Post a tweet to promote SunGenX's features.
            Here is an example:
            ```
            {template}
            ```
            - Use {language} to generate your tweet
            **DO NOT** simplely use the pattern of example.
            """
            result = await Console(self._create_tweet_team().run_stream(task=task))
            if isinstance(result, TaskResult):
                message = result.messages[-1]
            elif isinstance(result, TaskResponse):
                message = result.chat_message
            assert isinstance(message, TextMessage)
            content = message.content
            code, msg = await self.context_builder.create_tweet({"text": content})
            if code == 0:
                logger.info(f"promote_task: {content}")
            else:
                logger.error(f"error promote_task: {msg}")
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"error promote_task: {e}")

    async def show_case_task(self):
        # random delay 0-7 hour
        await asyncio.sleep(random.randint(0, 7) * 3600)
        logger.info("running show case task")
        template = ShowCaseTemplates[random.randint(0, len(ShowCaseTemplates) - 1)]
        now = datetime.now(UTC8)
        today = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=UTC8)
        try:
            tokens = json.loads(await self.sunpump_api_service.query_latest_tokens())
            if isinstance(tokens, str):
                raise RuntimeError(tokens)
            assert isinstance(tokens, List)
            for token in tokens:
                if isinstance(token, Dict) and "tweetUsername" in token and len(token["tweetUsername"]) > 0:
                    if not token["tokenCreatedInstant"] or token["tokenCreatedInstant"] < today.timestamp():
                        continue
                    content = template.format(token["tweetUsername"])
                    language = random.choice(languages)
                    if language != "english":
                        try:
                            prompt = f"use {language} to rewrite {content}" f"* only return content"
                            result = await self.text_model.create([UserMessage(content=prompt, source="user")])
                            content = result.content
                        except Exception as e:
                            logger.error(f"error show_case_task: {e}")
                    code, msg = await self.context_builder.create_tweet({"text": content})
                    if code == 0:
                        logger.info(f"show_case_task: {content}")
                    else:
                        logger.error(f"error show_case_task: {msg}")
                    return
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"error show_case_task {e}")


agent = SunAgentSystem()
scheduler = AsyncIOScheduler()
app = Quart(agent.agent_id)


@app.route("/tweet", methods=["POST"])
async def post_twitter() -> Response:
    data = await request.get_json()
    logger.info("/tweet post data=%s", data)
    code, text = await agent.context_builder.create_tweet(data)
    return jsonify({"error_code": code if code != 0 else 200, "text": text})


@app.route("/recover_time", methods=["POST"])
async def set_recover_time() -> Response:
    data = await request.get_json()
    logger.info("/recover_time post data=%s", data)
    if "timestamp" not in data:
        return jsonify({"error_code": 400, "text": "timestamp not found"})
    recover_time = int(data["timestamp"])
    if recover_time <= 0:
        code, text = await agent.context_builder.unset_recover_time()
        return jsonify({"error_code": code if code != 0 else 200, "text": text})
    else:
        code, text = await agent.context_builder.set_recover_time(recover_time)
    return jsonify({"error_code": code if code != 0 else 200, "text": text})


async def main() -> None:
    logger.info("SunAgent start")
    seconds = int(os.getenv("TOKEN_LAUNCH_INTERVAL_SECONDS", "90"))
    scheduler.add_job(
        agent.mentions_task,
        trigger="interval",
        seconds=seconds,
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
    )
    # scheduler.add_job(agent.daily_report, trigger="cron", hour=10, timezone=UTC8, max_instances=1)
    # week 1,3,5
    scheduler.add_job(agent.promote_task, trigger="cron", day_of_week=0, hour=12, timezone=UTC8, max_instances=1)
    scheduler.add_job(agent.promote_task, trigger="cron", day_of_week=2, hour=12, timezone=UTC8, max_instances=1)
    scheduler.add_job(agent.promote_task, trigger="cron", day_of_week=4, hour=12, timezone=UTC8, max_instances=1)
    # week 2,4
    scheduler.add_job(agent.show_case_task, trigger="cron", day_of_week=1, hour=12, timezone=UTC8, max_instances=1)
    scheduler.add_job(agent.show_case_task, trigger="cron", day_of_week=3, hour=12, timezone=UTC8, max_instances=1)
    scheduler.start()
    # await agent.context_builder.subscribe(agent.mention_stream)

    # start web app
    port = int(os.getenv("HTTP_PORT", "9529"))
    await app.run_task(host="0.0.0.0", port=port)


if __name__ == "__main__":
    asyncio.run(main())
