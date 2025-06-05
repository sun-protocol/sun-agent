#!/usr/bin/env python
# coding=utf-8
import logging
from typing import Any, List, Mapping

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from sunagent_app.cli import test_model

app = Flask(__name__)
CORS(app)


def setup_logging() -> None:
    """Function for setup logging"""
    # 设置日志的配置
    logging.basicConfig(
        level=logging.DEBUG,  # 设置日志级别为 DEBUG，也可以设置为 INFO, WARNING, ERROR, CRITICAL
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",  # 设置日志格式
        filename="app.log",  # 日志输出到文件，不设置这个参数则输出到标准输出（控制台）
        filemode="w",  # 'w' 表示写模式，'a' 表示追加模式
    )

    # 如果还想要将日志输出到控制台，可以添加一个 StreamHandler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # 设置控制台的日志级别
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(formatter)
    logging.getLogger("").addHandler(console_handler)
    logging.debug("logging start...")


def getImage(parameter: Mapping[str, Any]) -> str | None:
    if parameter:
        for x in parameter:
            if (
                x.get("name") == "image"  # type: ignore
                and x.get("type") == "image"  # type: ignore
            ):
                return x.get("value")  # type: ignore
    return None


@app.route("/spic", methods=["POST"])
async def spic() -> Response:
    data = request.json
    logging.info("/spic post data=%s", data)
    userInput = data.get("userInput")  # type: ignore
    user = data.get("user")  # type: ignore
    bucket_id = data.get("bucket_id")  # type: ignore
    if not bucket_id:
        bucket_id = ""

    if not userInput:
        return jsonify({"error_code": 400, "text": "No userInput provided"})
    elif not user:
        return jsonify({"error_code": 400, "text": "No user provided"})

    res: List[str] = [await test_model(userInput)]
    return jsonify(res)


@app.route("/spic_twitter", methods=["POST"])
async def spic_twitter() -> Response:
    data = request.json
    logging.info("/spic_twitter post data=%s", data)
    userInput = data.get("userInput")  # type: ignore
    user = data.get("user")  # type: ignore

    if not userInput:
        return jsonify({"error_code": 400, "text": "No userInput provided"})
    elif not user:
        return jsonify({"error_code": 400, "text": "No user provided"})

    res: List[str] = []
    return jsonify(res)


@app.route("/genimage", methods=["POST"])
async def genimage() -> Response:
    data = request.json
    prompts = data.get("prompts")  # type: ignore
    width = int(data.get("width"))  # type: ignore
    height = int(data.get("height"))  # type: ignore
    logging.info("/genimage post data=%s", data)

    if not prompts:
        return jsonify({"error_code": 400, "text": "No prompts provided"})
    elif not width:
        return jsonify({"error_code": 400, "text": "No width provided"})
    elif not height:
        return jsonify({"error_code": 400, "text": "No height provided"})

    res: List[str] = []
    return jsonify(res)


setup_logging()


def main() -> None:
    app.run(port=9529, debug=False)
