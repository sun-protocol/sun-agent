import asyncio
import logging
import os
import tempfile
from typing import Any, Optional, Sequence, Union

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import ChatMessage, TextMessage
from autogen_core import CancellationToken
from knowledge_storm import (  # type: ignore
    STORMWikiLMConfigs,
    STORMWikiRunner,
    STORMWikiRunnerArguments,
)
from knowledge_storm.lm import AzureOpenAIModel
from knowledge_storm.rm import (
    TavilySearchRM,
)
from sunagent_ext.secret_management.config import Config

from sunagent_app._constants import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class StormConfig:
    """Config loader for environment variables."""

    def __init__(self) -> None:
        # OpenAI parameters
        self.openai_api_key: str = ""
        self.openai_model_name: str = ""
        self.azure_api_base: str = ""
        self.azure_api_version: str = ""

        # String parameters
        self.output_dir: str = ""
        self.tavily_api_key: str = ""

        # Integer parameters
        self.max_thread_num: int = 2
        self.max_conv_turn: int = 2
        self.max_perspective: int = 2
        self.search_top_k: int = 2
        self.retrieve_top_k: int = 2

    async def initialize(self, config: Config) -> None:
        self.openai_api_key = await config.get_env("openai/OPENAI_API_KEY")
        self.openai_model_name = await config.get_env("openai/OPENAI_DEPLOYMENT")
        self.azure_api_base = await config.get_env("openai/OPENAI_ENDPOINT")
        self.azure_api_version = await config.get_env("openai/OPENAI_API_VERSION")

        # String parameters
        self.output_dir = await config.get_env("OUTPUT_DIR")
        self.tavily_api_key = await config.get_env("TAVILY_API_KEY")

        # Integer parameters
        self.max_thread_num = int(await config.get_env("MAX_THREAD_NUM", "2"))
        self.max_conv_turn = int(await config.get_env("MAX_CONV_TURN", "2"))
        self.max_perspective = int(await config.get_env("MAX_PERSPECTIVE", "2"))
        self.search_top_k = int(await config.get_env("SEARCH_TOP_K", "2"))
        self.retrieve_top_k = int(await config.get_env("RETRIEVE_TOP_K", "2"))

    def _str_to_bool(self, value: Union[str, bool]) -> bool:
        if isinstance(value, bool):
            return value
        return value.lower() in ("true", "1", "yes", "on")


class StormAgent(BaseChatAgent):
    """
    STORM Agent for generating wiki-style research reports.

    Stages: research, outline generation, article writing, and polishing.
    """

    def __init__(self, name: str, description: Optional[str] = None, config: Optional[StormConfig] = None):
        if description is None:
            description = """
            STORM Agent - research and writing assistant

            Capabilities:
            1) Deep research on a given topic
            2) Structured outline generation
            3) Wiki-style article writing
            4) Polishing and refinement

            Usage:
            Send a message with a topic; the agent will research and produce a full report.
            Behavior can be configured via environment variables.
            """

        super().__init__(name=name, description=description)
        self.config = config or StormConfig()
        self._setup_storm_runner()

    def _setup_storm_runner(self) -> None:
        """Initialize the STORM runner."""
        # Language model configuration
        self.lm_configs = STORMWikiLMConfigs()
        openai_kwargs = {
            "api_key": self.config.openai_api_key,
            "temperature": 1.0,
            "top_p": 0.9,
            "api_base": self.config.azure_api_base,
            "api_version": self.config.azure_api_version,
        }

        # Instantiate language models
        conv_simulator_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=500, **openai_kwargs)
        question_asker_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=500, **openai_kwargs)
        outline_gen_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=400, **openai_kwargs)
        article_gen_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=700, **openai_kwargs)
        article_polish_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=4000, **openai_kwargs)

        # Set model configs
        self.lm_configs.set_conv_simulator_lm(conv_simulator_lm)
        self.lm_configs.set_question_asker_lm(question_asker_lm)
        self.lm_configs.set_outline_gen_lm(outline_gen_lm)
        self.lm_configs.set_article_gen_lm(article_gen_lm)
        self.lm_configs.set_article_polish_lm(article_polish_lm)

        # Set retriever
        self.rm = TavilySearchRM(
            tavily_search_api_key=self.config.tavily_api_key,
            k=self.config.search_top_k,
            include_raw_content=True,
        )

    async def on_reset(self, cancellation_token: Optional[CancellationToken] = None) -> None:
        """Reset agent state."""
        pass

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        """Handle incoming messages and run STORM."""
        try:
            # Use the last message as the topic
            if not messages:
                return self._create_error_response("Please provide a topic.")

            last_message = messages[-1]
            # Extract content from different message types
            if isinstance(last_message, TextMessage):
                topic = last_message.content.strip()
            else:
                # Handle other message types or fall back to string representation
                topic = str(last_message).strip()

            # Create a temporary output directory
            with tempfile.TemporaryDirectory() as temp_dir:
                # Use temp dir if output_dir is not set
                output_dir = self.config.output_dir or temp_dir
                logger.info(f"stormAgent output directory: {output_dir}")
                # Engine arguments
                engine_args = STORMWikiRunnerArguments(
                    output_dir=output_dir,
                    max_conv_turn=self.config.max_conv_turn,
                    max_perspective=self.config.max_perspective,
                    search_top_k=self.config.search_top_k,
                    max_thread_num=self.config.max_thread_num,
                )
                # Create STORM runner
                runner = STORMWikiRunner(engine_args, self.lm_configs, self.rm)
                # Run STORM
                await asyncio.get_event_loop().run_in_executor(None, self._run_storm, runner, topic)
                # Get result
                result = StormAgent._get_storm_result(output_dir, topic)
                return Response(
                    chat_message=TextMessage(
                        content=result,
                        source=self.name,
                    )
                )

        except Exception as e:
            error_message = f"An error occurred during the STORM process: {str(e)}"
            return self._create_error_response(error_message)

    def _create_error_response(self, error_message: str) -> Response:
        return Response(
            chat_message=TextMessage(
                content=f"system internal error: {error_message}, EARLY_TERMINATE",
                source=self.name,
            )
        )

    def _run_storm(self, runner: Any, topic: str) -> None:
        """Synchronous wrapper to run STORM."""
        runner.run(
            topic=topic,
        )
        runner.post_run()
        runner.summary()

    @staticmethod
    def truncate_filename(filename: str, max_length: int = 125) -> str:
        if len(filename) > max_length:
            return filename[:max_length]
        return filename

    @staticmethod
    def _get_storm_result(output_dir: str, topic: str) -> str:
        """Read storm_gen_article_polished.txt and return its content."""
        article_dir_name = StormAgent.truncate_filename(topic.replace(" ", "_").replace("/", "_"))
        polished_path = os.path.join(output_dir, article_dir_name, "storm_gen_article_polished.txt")
        if os.path.exists(polished_path):
            with open(polished_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            raise RuntimeError("No polished article available")

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        """Message types produced by this agent."""
        return [TextMessage]
