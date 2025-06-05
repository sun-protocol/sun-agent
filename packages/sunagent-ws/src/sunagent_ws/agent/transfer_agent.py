import json
import re
from typing import (
    Callable,
    Optional,
    Sequence,
)

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import ChatMessage, TextMessage
from autogen_core import CancellationToken
from autogen_core.models import (
    ChatCompletionClient,
    SystemMessage,
    UserMessage,
)
from loguru import logger


def convert_result(res: str):
    pattern = r"```json\n(.*?)```"
    match = re.search(pattern, res, re.DOTALL)
    if match:
        json_str = match.group(1)
        json_res = json.loads(json_str)
        response = {
            "tokenAddress": json_res.get("tokenAddress", ""),
            "toAddress": json_res.get("toAddress", ""),
            "amount": json_res.get("amount", None),
            "symbol": json_res.get("symbol", ""),
        }
        return response
    else:
        return {}


class TransferAgent(BaseChatAgent):
    """An agent that process transfer business"""

    def __init__(
        self,
        name: str,
        *,
        description: str = """
        to start a transfer,if user need transfer
        """,
        model_client: ChatCompletionClient,
        start_transfer_func: Optional[Callable],
        system_message: str = """
        Prompt:
        Please provide the details of your transfer, including the transfer address, the amount, and the currency unit. For example, you can input something like: "Transfer to 1234567890, amount 500 USD."
        Example Input:
        "Transfer to 9876543210, amount 200 USDD."
        if you don't match field, don't return that field!
        Example Output:
        ```json
            {
                toAddress: str,
                amount: number,
                symbol: str,
            }
        ```
        """,
    ) -> None:
        super().__init__(name=name, description=description)
        self._model_client = model_client
        self.start_transfer_func = start_transfer_func
        self._system_message = SystemMessage(content=system_message)

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        """The types of messages that the code executor agent produces."""
        return (TextMessage,)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        assert isinstance(messages[-1], TextMessage)
        content = messages[-1].content

        res = await self._model_client.create(
            messages=[self._system_message, UserMessage(content=content, source=self.name)]
        )
        params = convert_result(res.content.strip())
        ms = {
            "name": "transfer",
            "args": params,
        }
        logger.info(f"transfer params: {params}")
        # result = await self.start_transfer_func(json.dumps(ms, ensure_ascii=False))
        final_msg = f"""

        ```json
            {json.dumps(ms, ensure_ascii=False)}
        ```
        """
        return Response(chat_message=TextMessage(content=final_msg, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass
