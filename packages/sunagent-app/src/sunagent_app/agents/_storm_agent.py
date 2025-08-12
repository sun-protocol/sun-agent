import asyncio
import logging
import os
import tempfile
from typing import Sequence

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import TextMessage
from knowledge_storm import (
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
    """从环境变量中获取配置的配置类"""

    def __init__(self):
        # openai参数
        self.openai_api_key = ""
        self.openai_model_name = ""
        self.azure_api_base = ""
        self.azure_api_version = ""

        # 字符串类型参数
        self.output_dir = ""
        self.tavily_api_key = ""

        # 整数类型参数
        self.max_thread_num = ""
        self.max_conv_turn = ""
        self.max_perspective = ""
        self.search_top_k = ""
        self.retrieve_top_k = ""

    async def initialize(self, config: Config):
        self.openai_api_key = await config.get_env("openai/OPENAI_API_KEY")
        self.openai_model_name = await config.get_env("openai/OPENAI_DEPLOYMENT")
        self.azure_api_base = await config.get_env("openai/OPENAI_ENDPOINT")
        self.azure_api_version = await config.get_env("openai/OPENAI_API_VERSION")

        # 字符串类型参数
        self.output_dir = await config.get_env("OUTPUT_DIR")
        self.tavily_api_key = await config.get_env("TAVILY_API_KEY")

        # 整数类型参数
        self.max_thread_num = int(await config.get_env("MAX_THREAD_NUM", "2"))
        self.max_conv_turn = int(await config.get_env("MAX_CONV_TURN", "2"))
        self.max_perspective = int(await config.get_env("MAX_PERSPECTIVE", "2"))
        self.search_top_k = int(await config.get_env("SEARCH_TOP_K", "2"))
        self.retrieve_top_k = int(await config.get_env("RETRIEVE_TOP_K", "2"))

    def _str_to_bool(self, value):
        """将字符串转换为布尔值"""
        if isinstance(value, bool):
            return value
        return value.lower() in ("true", "1", "yes", "on")


class StormAgent(BaseChatAgent):
    """
    STORM Agent for autogen - 生成维基百科风格的深度研究报告

    该Agent可以基于给定主题进行全面的研究，生成结构化的文章内容。
    支持多个阶段：研究、大纲生成、文章写作和内容润色。
    """

    def __init__(self, name: str, description: str = None, config: StormConfig = None):
        if description is None:
            description = """
            STORM Agent - 专业的研究和写作助手

            功能：
            1. 基于主题进行深度研究
            2. 生成结构化的文章大纲
            3. 写作详细的Wiki风格文章
            4. 内容润色和优化

            使用方式：
            发送包含主题的消息，Agent会自动进行研究并生成完整的报告。
            可以通过环境变量配置各个阶段的开关。
            """

        super().__init__(name=name, description=description)
        self.config = config or StormConfig()
        self._setup_storm_runner()

    def _setup_storm_runner(self):
        """设置STORM运行器"""
        # 设置语言模型配置
        self.lm_configs = STORMWikiLMConfigs()
        openai_kwargs = {
            "api_key": self.config.openai_api_key,
            "temperature": 1.0,
            "top_p": 0.9,
            "api_base": self.config.azure_api_base,
            "api_version": self.config.azure_api_version,
        }

        # 创建各种语言模型实例
        conv_simulator_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=500, **openai_kwargs)
        question_asker_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=500, **openai_kwargs)
        outline_gen_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=400, **openai_kwargs)
        article_gen_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=700, **openai_kwargs)
        article_polish_lm = AzureOpenAIModel(model=self.config.openai_model_name, max_tokens=4000, **openai_kwargs)

        # 设置模型配置
        self.lm_configs.set_conv_simulator_lm(conv_simulator_lm)
        self.lm_configs.set_question_asker_lm(question_asker_lm)
        self.lm_configs.set_outline_gen_lm(outline_gen_lm)
        self.lm_configs.set_article_gen_lm(article_gen_lm)
        self.lm_configs.set_article_polish_lm(article_polish_lm)

        # 设置检索器
        self.rm = TavilySearchRM(
            tavily_search_api_key=self.config.tavily_api_key,
            k=self.config.search_top_k,
            include_raw_content=True,
        )

    async def on_reset(self, cancellation_token=None):
        """重置Agent状态"""
        pass

    async def on_messages(self, messages: Sequence[TextMessage], cancellation_token=None, **kwargs) -> Response:
        """处理输入消息并运行STORM"""
        try:
            # 获取最后一条消息作为主题
            if not messages:
                return self._create_error_response("Please provide a topic.")

            last_message = messages[-1]
            topic = last_message.content.strip()

            # 创建临时输出目录
            with tempfile.TemporaryDirectory() as temp_dir:
                # 如果未设置output_dir，则使用临时目录
                output_dir = self.config.output_dir or temp_dir
                logger.info(f"stormAgent output directory: {output_dir}")
                # 设置引擎参数
                engine_args = STORMWikiRunnerArguments(
                    output_dir=output_dir,
                    max_conv_turn=self.config.max_conv_turn,
                    max_perspective=self.config.max_perspective,
                    search_top_k=self.config.search_top_k,
                    max_thread_num=self.config.max_thread_num,
                )
                # 创建STORM运行器
                runner = STORMWikiRunner(engine_args, self.lm_configs, self.rm)
                # 运行STORM
                await asyncio.get_event_loop().run_in_executor(None, self._run_storm, runner, topic)
                # 获取结果
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

    def _run_storm(self, runner: STORMWikiRunner, topic: str):
        """运行STORM的同步方法"""
        runner.run(
            topic=topic,
        )
        runner.post_run()
        runner.summary()

    @staticmethod
    def truncate_filename(filename, max_length=125):
        if len(filename) > max_length:
            return filename[:max_length]
        return filename

    @staticmethod
    def _get_storm_result(output_dir: str, topic: str) -> str:
        """只读取 storm_gen_article_polished.txt 文件并返回内容"""
        article_dir_name = StormAgent.truncate_filename(topic.replace(" ", "_").replace("/", "_"))
        polished_path = os.path.join(output_dir, article_dir_name, "storm_gen_article_polished.txt")
        if os.path.exists(polished_path):
            with open(polished_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            raise RuntimeError("No polished article available")

    @property
    def produced_message_types(self):
        """返回此Agent产生的消息类型"""
        return ["TextMessage"]
